"""
LLM Sentinel Client — multi-provider with automatic fallback chain.

Priority order (free-first):
  1. Ollama  (local, 100% free — no key)   run: ollama pull llama3.2
  2. Gemini  (free cloud, 1500 req/day)     get key: aistudio.google.com
  3. Groq    (free tier, needs key)
  4. OpenAI  (paid, gpt-4o-mini)
  5. Anthropic (paid, claude-3-5-haiku)

Retries up to 3 times per provider with exponential backoff.
Falls back to next provider only on provider-level failure.
"""
from __future__ import annotations

import json
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from macropulse.config import settings
from macropulse.logger import get_logger
from macropulse.models import TradeSignal

log = get_logger(__name__)


class LLMProviderError(Exception):
    """Raised when all LLM providers fail."""


class LLMSentinelClient:
    """
    Multi-provider LLM client — free sources first, paid as optional fallback.
    Returns a validated TradeSignal Pydantic model.
    """

    def __init__(self) -> None:
        self._providers: list[tuple[str, str, Any]] = []
        self._build_provider_chain()

    def _build_provider_chain(self) -> None:
        """
        Build ordered list of (name, model, client) tuples.
        Free providers are always attempted first.
        """
        # ── 1. Ollama — 100% free, local, no API key ──────────────────────
        if settings.ollama_enabled:
            # Test connectivity lazily (we'll detect failure at call time)
            self._providers.append(("ollama", settings.ollama_model, None))
            log.info(
                "llm_client.provider_registered",
                provider="ollama",
                model=settings.ollama_model,
                url=settings.ollama_base_url,
                note="FREE_LOCAL",
            )

        # ── 2. Google Gemini — free tier (1500 req/day, no CC needed) ─────
        if settings.gemini_api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.gemini_api_key)
                client = genai.GenerativeModel(settings.gemini_model)
                self._providers.append(("gemini", settings.gemini_model, client))
                log.info(
                    "llm_client.provider_registered",
                    provider="gemini",
                    model=settings.gemini_model,
                    note="FREE_CLOUD_1500/day",
                )
            except ImportError:
                log.warning("llm_client.gemini_not_installed",
                            hint="pip install google-generativeai")

        # ── 3. Groq — generous free tier (needs key, no CC) ───────────────
        if settings.groq_api_key:
            try:
                from groq import AsyncGroq
                client = AsyncGroq(api_key=settings.groq_api_key)
                self._providers.append(("groq", settings.groq_model, client))
                log.info("llm_client.provider_registered", provider="groq",
                         model=settings.groq_model, note="FREE_TIER_KEY_NEEDED")
            except ImportError:
                log.warning("llm_client.groq_not_installed")

        # ── 4. OpenAI — paid ────────────────────────────────────────────────
        if settings.openai_api_key:
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.openai_api_key)
                self._providers.append(("openai", settings.openai_model, client))
                log.info("llm_client.provider_registered", provider="openai",
                         model=settings.openai_model)
            except ImportError:
                log.warning("llm_client.openai_not_installed")

        # ── 5. Anthropic — paid ─────────────────────────────────────────────
        if settings.anthropic_api_key:
            try:
                from anthropic import AsyncAnthropic
                client = AsyncAnthropic(api_key=settings.anthropic_api_key)
                self._providers.append(("anthropic", settings.anthropic_model, client))
                log.info("llm_client.provider_registered", provider="anthropic",
                         model=settings.anthropic_model)
            except ImportError:
                log.warning("llm_client.anthropic_not_installed")

        if not self._providers:
            raise RuntimeError(
                "No LLM provider available.\n"
                "FREE OPTIONS:\n"
                "  • Ollama (local): https://ollama.com → 'ollama pull llama3.2'\n"
                "  • Gemini (cloud): https://aistudio.google.com → set GEMINI_API_KEY\n"
                "PAID OPTIONS (optional fallbacks):\n"
                "  • Set GROQ_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY"
            )

    # ── Public Interface ─────────────────────────────────────────────────

    async def evaluate(self, system_prompt: str, user_prompt: str) -> TradeSignal:
        """
        Send prompts through the provider chain.
        Returns a validated TradeSignal on success.
        Raises LLMProviderError if all providers fail.
        """
        last_exc: Exception | None = None

        for provider_name, model, client in self._providers:
            try:
                raw_json = await self._call_with_retry(
                    provider_name, model, client, system_prompt, user_prompt
                )
                signal = self._parse_and_validate(raw_json, provider_name, model)
                log.info(
                    "llm_client.success",
                    provider=provider_name,
                    event=signal.event_title[:60],
                    impact=signal.impact_rating,
                )
                return signal
            except Exception as exc:
                log.warning("llm_client.provider_failed",
                            provider=provider_name, error=str(exc)[:200])
                last_exc = exc

        raise LLMProviderError(f"All LLM providers failed. Last: {last_exc}")

    # ── Provider Dispatch ────────────────────────────────────────────────

    async def _call_with_retry(
        self, provider_name: str, model: str, client: Any,
        system_prompt: str, user_prompt: str,
    ) -> str:
        if provider_name == "ollama":
            return await self._call_ollama_with_retry(model, system_prompt, user_prompt)
        elif provider_name == "gemini":
            return await self._call_gemini_with_retry(client, system_prompt, user_prompt)
        elif provider_name == "groq":
            return await self._call_groq_with_retry(client, model, system_prompt, user_prompt)
        elif provider_name == "openai":
            return await self._call_openai_with_retry(client, model, system_prompt, user_prompt)
        elif provider_name == "anthropic":
            return await self._call_anthropic_with_retry(client, model, system_prompt, user_prompt)
        raise ValueError(f"Unknown provider: {provider_name}")

    # ── Ollama (free, local) ─────────────────────────────────────────────

    @retry(retry=retry_if_exception_type(Exception), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def _call_ollama_with_retry(
        self, model: str, system_prompt: str, user_prompt: str
    ) -> str:
        """
        Call local Ollama instance via REST API.
        No API key needed — runs entirely on your machine.
        Ensure Ollama is running: `ollama serve`
        Pull model first:          `ollama pull llama3.2`
        """
        import aiohttp

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",          # Ollama native JSON mode
            "options": {"temperature": 0.1, "num_predict": 1500},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{settings.ollama_base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),  # local inference can be slow
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["message"]["content"]

    # ── Gemini (free cloud, 1500 req/day) ────────────────────────────────

    @retry(retry=retry_if_exception_type(Exception), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=2, max=15), reraise=True)
    async def _call_gemini_with_retry(
        self, client: Any, system_prompt: str, user_prompt: str
    ) -> str:
        """
        Google Gemini 1.5 Flash — free tier, 1500 requests/day.
        Get API key at: https://aistudio.google.com/app/apikey
        """
        import asyncio

        # google-generativeai is synchronous — run in executor
        loop = asyncio.get_event_loop()
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        response = await loop.run_in_executor(
            None,
            lambda: client.generate_content(
                full_prompt,
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 1500,
                    "response_mime_type": "application/json",  # enforce JSON output
                },
            ),
        )
        return response.text

    # ── Groq (free tier, needs key) ──────────────────────────────────────

    @retry(retry=retry_if_exception_type(Exception), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def _call_groq_with_retry(
        self, client: Any, model: str, system_prompt: str, user_prompt: str
    ) -> str:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    # ── OpenAI (paid) ────────────────────────────────────────────────────

    @retry(retry=retry_if_exception_type(Exception), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def _call_openai_with_retry(
        self, client: Any, model: str, system_prompt: str, user_prompt: str
    ) -> str:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    # ── Anthropic (paid) ─────────────────────────────────────────────────

    @retry(retry=retry_if_exception_type(Exception), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def _call_anthropic_with_retry(
        self, client: Any, model: str, system_prompt: str, user_prompt: str
    ) -> str:
        message = await client.messages.create(
            model=model,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.1,
        )
        return message.content[0].text if message.content else ""

    # ── Response Parsing ─────────────────────────────────────────────────

    def _parse_and_validate(
        self, raw_json: str, provider: str, model: str
    ) -> TradeSignal:
        text = raw_json.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data: dict = json.loads(text)
        data["llm_provider"] = provider
        data["llm_model"] = model
        for pair in data.get("actionable_pairs", []):
            pair.setdefault("lot_size_25usd_risk", 0.0)

        return TradeSignal.model_validate(data)
