"""
Telegram bot alert publisher.

Features:
- Async message dispatch via python-telegram-bot v21
- Rate limiting: max 20 messages/minute (Telegram flood limit is 30/s per chat)
- Automatic retry on RetryAfter (429) errors
- HTML parse mode for rich formatting
- Test command: python -m macropulse.telegram.bot --test
"""
from __future__ import annotations

import asyncio
import sys
import time
from collections import deque

from telegram import Bot
from telegram.error import RetryAfter, TelegramError

from macropulse.config import settings
from macropulse.logger import get_logger

log = get_logger(__name__)

_MAX_MSGS_PER_MINUTE = 20
_MSG_LEN_LIMIT = 4096  # Telegram HTML message character limit


class TelegramAlerter:
    """
    Async Telegram bot publisher with built-in rate limiting.
    """

    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._chat_id = settings.telegram_chat_id
        self._dry_run = settings.dry_run
        # Sliding window for rate limiting
        self._sent_timestamps: deque[float] = deque(maxlen=_MAX_MSGS_PER_MINUTE)

    async def start(self) -> None:
        if not settings.has_telegram:
            log.warning("telegram.not_configured", dry_run=self._dry_run)
            return
        self._bot = Bot(token=settings.telegram_bot_token)  # type: ignore[arg-type]
        try:
            info = await self._bot.get_me()
            log.info("telegram.connected", bot_username=info.username)
        except TelegramError as exc:
            log.error("telegram.connection_failed", error=str(exc))
            self._bot = None

    async def stop(self) -> None:
        if self._bot:
            await self._bot.close()

    async def send_html(self, text: str) -> bool:
        """
        Send an HTML-formatted message to the configured chat.

        Returns True on success, False on failure.
        """
        if self._dry_run:
            log.info("telegram.dry_run_suppressed", preview=text[:120])
            return True

        if not self._bot:
            log.warning("telegram.no_bot_connection")
            return False

        # Truncate if over limit
        if len(text) > _MSG_LEN_LIMIT:
            text = text[: _MSG_LEN_LIMIT - 50] + "\n...<i>[truncated]</i>"

        await self._wait_for_rate_limit()
        return await self._send_with_retry(text)

    async def _wait_for_rate_limit(self) -> None:
        """Block until we're within the 20/min rate limit."""
        now = time.monotonic()
        window_start = now - 60
        # Drop timestamps older than 1 minute
        while self._sent_timestamps and self._sent_timestamps[0] < window_start:
            self._sent_timestamps.popleft()

        if len(self._sent_timestamps) >= _MAX_MSGS_PER_MINUTE:
            wait_until = self._sent_timestamps[0] + 60
            sleep_secs = wait_until - now
            if sleep_secs > 0:
                log.debug("telegram.rate_limited", sleep=round(sleep_secs, 1))
                await asyncio.sleep(sleep_secs)

    async def _send_with_retry(self, text: str, attempt: int = 0) -> bool:
        """Send with exponential backoff on RetryAfter errors."""
        try:
            await self._bot.send_message(  # type: ignore[union-attr]
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            self._sent_timestamps.append(time.monotonic())
            log.info("telegram.sent", chars=len(text))
            return True
        except RetryAfter as exc:
            wait = exc.retry_after + 1
            log.warning("telegram.retry_after", wait=wait)
            await asyncio.sleep(wait)
            if attempt < 4:
                return await self._send_with_retry(text, attempt + 1)
            log.error("telegram.send_failed_after_retries")
            return False
        except TelegramError as exc:
            log.error("telegram.send_error", error=str(exc))
            return False


# ── CLI Test Mode ─────────────────────────────────────────────────────────────

async def _run_test() -> None:
    from macropulse.telegram.formatter import format_startup_message

    alerter = TelegramAlerter()
    await alerter.start()
    msg = format_startup_message(
        provider="groq/llama-3-3-70b-versatile",
        mode="mock",
        dry_run=settings.dry_run,
    )
    success = await alerter.send_html(msg)
    if success:
        print("✅ Test message sent successfully!")
    else:
        print("❌ Failed to send test message. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
    await alerter.stop()


if __name__ == "__main__":
    if "--test" in sys.argv:
        from macropulse.logger import configure_logging
        configure_logging()
        asyncio.run(_run_test())
