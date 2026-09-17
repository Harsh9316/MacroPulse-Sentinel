"""
Twitter/X Account Monitor

Three operating modes (configured via TWITTER_MODE env var):

1. api   — Twitter API v2 Filtered Stream (requires Bearer Token)
           Monitors official central bank & financial news handles in real-time.

2. rss   — Nitter RSS fallback (no API key needed).
           Polls nitter.privacydev.net/{handle}/rss every 30s per account.

3. disabled — Twitter monitoring is off; rely on RSS feeds only.

Official Twitter/X Handles Monitored:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Central Banks:
  @federalreserve     — US Federal Reserve (official)
  @ecb                — European Central Bank (official)
  @bankofengland      — Bank of England (official)
  @Bank_of_Japan_e    — Bank of Japan (English, official)
  @SNB_BNS            — Swiss National Bank (official)
  @RBAInfo            — Reserve Bank of Australia (official)
  @bankofcanada       — Bank of Canada (official)

Key Officials:
  @Lagarde            — Christine Lagarde, ECB President
  @JanetYellen        — Janet Yellen, US Treasury Secretary
  @BoasticFed         — Raphael Bostic, Atlanta Fed President
  @Nick_Timiraos      — Nick Timiraos (WSJ), "Fed Whisperer"
  @GregIpWsj          — Greg Ip (WSJ), Chief Economics Commentator

Financial News Wires:
  @Reuters            — Reuters (general)
  @ReutersBiz         — Reuters Business
  @Bloomberg          — Bloomberg
  @markets            — Bloomberg Markets
  @FinancialJuice     — FinancialJuice (ultra-low-latency wire)
  @ForexLive          — ForexLive
  @WSJmarkets         — Wall Street Journal Markets
  @CNBC               — CNBC
  @zerohedge          — ZeroHedge
  @FXStreet           — FXStreet
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timezone
from typing import Any

import aiohttp

from macropulse.config import NITTER_BASE_URL, TWITTER_WATCHLIST, settings
from macropulse.dedup import dedup_cache
from macropulse.logger import get_logger
from macropulse.models import NewsHeadline, SourceType

log = get_logger(__name__)

_NITTER_POLL_INTERVAL = 30  # seconds per account (RSS fallback)
_TWITTER_STREAM_URL = "https://api.twitter.com/2/tweets/search/stream"
_TWITTER_RULES_URL = "https://api.twitter.com/2/tweets/search/stream/rules"

# Keywords to build Twitter filtered stream rules
_STREAM_KEYWORDS = [
    "rate decision", "interest rate", "inflation", "CPI", "NFP",
    "nonfarm payrolls", "FOMC", "hawkish", "dovish", "monetary policy",
    "quantitative tightening", "QT", "QE", "yield curve", "federal funds",
    "central bank", "Jackson Hole",
]

# All handles to monitor
_ALL_HANDLES = list(TWITTER_WATCHLIST.keys())


def _build_stream_rule() -> str:
    """Build a Twitter filtered stream rule for all watched accounts."""
    handle_filter = " OR ".join(f"from:{h}" for h in _ALL_HANDLES)
    keyword_filter = " OR ".join(f'"{kw}"' for kw in _STREAM_KEYWORDS[:10])  # API limit
    return f"({handle_filter}) ({keyword_filter})"


class TwitterMonitor:
    """
    Monitors official Twitter/X accounts for market-moving posts.
    Supports API v2 filtered stream and Nitter RSS fallback modes.
    """

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self._queue = queue
        self._session: aiohttp.ClientSession | None = None
        self._mode = settings.twitter_mode

    async def start(self) -> None:
        if self._mode == "disabled":
            log.info("twitter_monitor.disabled")
            return

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "MacroPulse-Sentinel/1.0"},
        )

        if self._mode == "api" and settings.twitter_bearer_token:
            log.info("twitter_monitor.started", mode="api_v2_stream")
            await self._run_api_stream()
        else:
            log.info("twitter_monitor.started", mode="nitter_rss_fallback",
                     accounts=len(_ALL_HANDLES))
            await self._run_nitter_loop()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    # ── Twitter API v2 Filtered Stream ───────────────────────────────────

    async def _run_api_stream(self) -> None:
        """
        Connect to Twitter Filtered Stream and process incoming tweets.
        Reconnects automatically with exponential backoff on errors.
        """
        headers = {"Authorization": f"Bearer {settings.twitter_bearer_token}"}
        await self._setup_stream_rules(headers)

        backoff = 1
        while True:
            try:
                async with self._session.get(  # type: ignore[union-attr]
                    _TWITTER_STREAM_URL,
                    headers=headers,
                    params={"tweet.fields": "created_at,author_id,text",
                            "expansions": "author_id",
                            "user.fields": "username,name"},
                ) as resp:
                    if resp.status == 429:
                        log.warning("twitter_stream.rate_limited", backoff=backoff)
                        await asyncio.sleep(backoff * 60)
                        backoff = min(backoff * 2, 60)
                        continue
                    resp.raise_for_status()
                    backoff = 1  # reset on success
                    async for line in resp.content:
                        line = line.strip()
                        if not line:
                            continue
                        await self._process_stream_tweet(line)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("twitter_stream.disconnect", error=str(exc), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    async def _setup_stream_rules(self, headers: dict[str, str]) -> None:
        """Delete existing rules and set up fresh filtered stream rules."""
        import json

        # Get existing rules
        async with self._session.get(_TWITTER_RULES_URL, headers=headers) as resp:  # type: ignore[union-attr]
            existing = await resp.json()
        existing_ids = [r["id"] for r in existing.get("data", [])]

        if existing_ids:
            await self._session.post(  # type: ignore[union-attr]
                _TWITTER_RULES_URL,
                headers={**headers, "Content-Type": "application/json"},
                json={"delete": {"ids": existing_ids}},
            )

        # Add new rule
        rule = _build_stream_rule()
        payload = {"add": [{"value": rule, "tag": "macropulse_main"}]}
        async with self._session.post(  # type: ignore[union-attr]
            _TWITTER_RULES_URL,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        ) as resp:
            result = await resp.json()
            log.info("twitter_stream.rules_set", result=str(result)[:200])

    async def _process_stream_tweet(self, raw: bytes) -> None:
        import json
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        tweet = data.get("data", {})
        text = tweet.get("text", "")
        if not text:
            return

        # Extract username from includes
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
        author_id = tweet.get("author_id", "")
        username = users.get(author_id, {}).get("username", "unknown")

        await self._dispatch_headline(
            text=text,
            handle=username,
            url=f"https://twitter.com/{username}/status/{tweet.get('id','')}",
        )

    # ── Nitter RSS Fallback ──────────────────────────────────────────────

    async def _run_nitter_loop(self) -> None:
        """Poll Nitter RSS for each watched handle with staggered intervals."""
        import feedparser

        async def poll_account(handle: str, meta: dict) -> None:
            while True:
                try:
                    await self._poll_nitter_rss(handle, meta)
                except Exception as exc:
                    log.debug("twitter_nitter.error", handle=handle, error=str(exc))
                jitter = random.uniform(0.8, 1.2)
                await asyncio.sleep(_NITTER_POLL_INTERVAL * jitter)

        async with asyncio.TaskGroup() as tg:
            for handle, meta in TWITTER_WATCHLIST.items():
                tg.create_task(poll_account(handle, meta))

    async def _poll_nitter_rss(self, handle: str, meta: dict) -> None:
        import feedparser

        url = f"{NITTER_BASE_URL}/{handle}/rss"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:  # type: ignore[union-attr]
                if resp.status != 200:
                    return
                content = await resp.read()
        except Exception:
            return

        parsed = feedparser.parse(content)
        for entry in parsed.entries[:10]:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            await self._dispatch_headline(
                text=title,
                handle=handle,
                url=link,
            )

    # ── Common Dispatch ──────────────────────────────────────────────────

    async def _dispatch_headline(self, text: str, handle: str, url: str) -> None:
        """Filter, dedup, and dispatch a tweet as a NewsHeadline."""
        from macropulse.config import HIGH_IMPACT_EVENTS

        text_lower = text.lower()
        relevant = any(kw in text_lower for kw in HIGH_IMPACT_EVENTS) or any(
            sym in text_lower
            for sym in ("gold", "xau", "nasdaq", "ndx", "yen", "dollar", "rate", "inflation")
        )
        if not relevant:
            return

        is_new = await dedup_cache.check_and_mark(text)
        if not is_new:
            return

        meta = TWITTER_WATCHLIST.get(handle.lower(), {})
        h = hashlib.sha256(text.strip().encode()).hexdigest()
        headline = NewsHeadline(
            headline_id=h,
            title=text.strip(),
            url=url or None,
            source_name=meta.get("label", f"@{handle}"),
            source_type=SourceType.TWITTER,
            twitter_handle=f"@{handle}",
            ingested_at=datetime.now(timezone.utc),
            raw_text=text,
        )
        log.info(
            "twitter_monitor.tweet_dispatched",
            handle=f"@{handle}",
            tier=meta.get("tier", "?"),
            preview=text[:100],
        )
        await self._queue.put(headline)
