"""
Tests for the deduplication cache.
"""
import pytest
from macropulse.dedup import DedupCache


@pytest.fixture
async def cache(tmp_path):
    """Fresh in-memory dedup cache backed by a temp SQLite file."""
    import os
    os.environ["DEDUP_DB_PATH"] = str(tmp_path / "test_dedup.db")
    c = DedupCache()
    # Override path directly
    c._db_path = str(tmp_path / "test_dedup.db")
    await c.start()
    yield c
    await c.stop()


@pytest.mark.asyncio
async def test_new_item_not_seen(cache):
    assert not await cache.is_seen("NFP beats expectations, USD surges")


@pytest.mark.asyncio
async def test_mark_and_seen(cache):
    text = "Fed raises rates by 75bp — hawkish surprise"
    await cache.mark_seen(text)
    assert await cache.is_seen(text)


@pytest.mark.asyncio
async def test_check_and_mark_idempotent(cache):
    text = "ECB holds rates unchanged"
    # First call: new → True
    result1 = await cache.check_and_mark(text)
    assert result1 is True
    # Second call: duplicate → False
    result2 = await cache.check_and_mark(text)
    assert result2 is False


@pytest.mark.asyncio
async def test_hash_is_case_and_whitespace_insensitive(cache):
    text1 = "BoJ Rate Decision — SURPRISE HIKE"
    text2 = "  boj rate decision — surprise hike  "
    await cache.mark_seen(text1)
    # Different case/whitespace but same hash
    assert await cache.is_seen(text2)


@pytest.mark.asyncio
async def test_different_texts_are_independent(cache):
    await cache.mark_seen("NFP beats")
    assert not await cache.is_seen("CPI misses")


@pytest.mark.asyncio
async def test_cleanup_expired(cache):
    """Force-insert an old entry and verify cleanup removes it."""
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    async with aiosqlite.connect(cache._db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO seen_events (hash, seen_at) VALUES (?, ?)",
            ("deadbeef" * 8, old_time),
        )
        await db.commit()

    deleted = await cache.cleanup_expired()
    assert deleted == 1
