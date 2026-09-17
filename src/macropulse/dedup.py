"""
SQLite-backed deduplication cache.

Uses SHA-256 hashes of raw event/headline text with a 24-hour TTL
to prevent the same event from triggering multiple LLM evaluations.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import aiosqlite

from macropulse.config import settings
from macropulse.logger import get_logger

log = get_logger(__name__)

_TTL_HOURS = 24
_CLEANUP_INTERVAL_SECONDS = 3600  # run cleanup every hour


class DedupCache:
    """Async SQLite dedup store with 24-hour TTL."""

    def __init__(self) -> None:
        self._db_path = str(settings.dedup_db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_events (
                hash        TEXT PRIMARY KEY,
                seen_at     TEXT NOT NULL
            )
            """
        )
        await self._conn.commit()
        log.info("dedup_cache.started", db=self._db_path)

    async def stop(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── Public API ───────────────────────────────────────────────────────

    @staticmethod
    def compute_hash(text: str) -> str:
        """Return SHA-256 hex digest of the given text."""
        return hashlib.sha256(text.strip().lower().encode()).hexdigest()

    async def is_seen(self, text: str) -> bool:
        """Return True if this text has been seen within the TTL window."""
        h = self.compute_hash(text)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_TTL_HOURS)).isoformat()
        async with self._lock:
            async with self._conn.execute(  # type: ignore[union-attr]
                "SELECT 1 FROM seen_events WHERE hash = ? AND seen_at > ?",
                (h, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
        return row is not None

    async def mark_seen(self, text: str) -> None:
        """Record a text hash with current timestamp."""
        h = self.compute_hash(text)
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            await self._conn.execute(  # type: ignore[union-attr]
                "INSERT OR REPLACE INTO seen_events (hash, seen_at) VALUES (?, ?)",
                (h, now),
            )
            await self._conn.commit()  # type: ignore[union-attr]
        log.debug("dedup_cache.marked", hash=h[:12])

    async def check_and_mark(self, text: str) -> bool:
        """
        Atomically check + mark. Returns True if the item is NEW (not seen).
        Returns False if duplicate (caller should skip processing).
        """
        if await self.is_seen(text):
            log.debug("dedup_cache.duplicate_dropped", preview=text[:80])
            return False
        await self.mark_seen(text)
        return True

    async def cleanup_expired(self) -> int:
        """Remove entries older than TTL. Returns number of rows deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_TTL_HOURS)).isoformat()
        async with self._lock:
            cursor = await self._conn.execute(  # type: ignore[union-attr]
                "DELETE FROM seen_events WHERE seen_at <= ?", (cutoff,)
            )
            await self._conn.commit()  # type: ignore[union-attr]
            deleted = cursor.rowcount
        if deleted:
            log.info("dedup_cache.cleanup", deleted=deleted)
        return deleted

    async def run_cleanup_loop(self) -> None:
        """Background task: periodically purge expired entries."""
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            try:
                await self.cleanup_expired()
            except Exception as exc:
                log.warning("dedup_cache.cleanup_error", error=str(exc))


# Module-level singleton — shared across all workers
dedup_cache = DedupCache()
