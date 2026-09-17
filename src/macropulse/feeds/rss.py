"""
Async RSS feed monitor for central bank and financial news wires.

Polls multiple RSS feeds concurrently at configurable intervals.
Uses feedparser for parsing, aiohttp for async fetching.
Publishes NewsHeadline objects to the shared event queue.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp
import feedparser

from macropulse.config import HIGH_IMPACT_EVENTS, settings
from macropulse.dedup import dedup_cache
from macropulse.logger import get_logger
from macropulse.models import NewsHeadline, SourceType

log = get_logger(__name__)

# ── RSS Feed Registry ─────────────────────────────────────────────────────────
# Each entry: (name, url, tier, relevance_keywords_override_or_None)
RSS_FEEDS: list[dict[str, Any]] = [
    # ── Central Bank Official RSS ─────────────────────────────────────────
    {
        "name": "Federal Reserve Press Releases",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "tier": "CRITICAL",
        "source_type": SourceType.RSS,
        "twitter": "@federalreserve",
    },
    {
        "name": "Federal Reserve Speeches",
        "url": "https://www.federalreserve.gov/feeds/speeches.xml",
        "tier": "CRITICAL",
        "source_type": SourceType.RSS,
        "twitter": "@federalreserve",
    },
    {
        "name": "ECB Press Releases",
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "tier": "CRITICAL",
        "source_type": SourceType.RSS,
        "twitter": "@ecb / @Lagarde",
    },
    {
        "name": "Bank of England News",
        "url": "https://www.bankofengland.co.uk/rss/news",
        "tier": "CRITICAL",
        "source_type": SourceType.RSS,
        "twitter": "@bankofengland",
    },
    {
        "name": "Bank of Japan Announcements",
        "url": "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "tier": "CRITICAL",
        "source_type": SourceType.RSS,
        "twitter": "@Bank_of_Japan_e",
    },
    {
        "name": "US Treasury News",
        "url": "https://home.treasury.gov/news/press-releases.rss",
        "tier": "HIGH",
        "source_type": SourceType.RSS,
        "twitter": "@USTreasury / @JanetYellen",
    },
    # ── Financial News Wires ──────────────────────────────────────────────
    {
        "name": "Reuters Top News",
        "url": "https://feeds.reuters.com/reuters/topNews",
        "tier": "HIGH",
        "source_type": SourceType.RSS,
        "twitter": "@Reuters",
    },
    {
        "name": "Reuters Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "tier": "HIGH",
        "source_type": SourceType.RSS,
        "twitter": "@ReutersBiz",
    },
    {
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/USmarketsnews",
        "tier": "HIGH",
        "source_type": SourceType.RSS,
        "twitter": "@ReutersBiz",
    },
    {
        "name": "ForexLive",
        "url": "https://www.forexlive.com/feed/news",
        "tier": "HIGH",
        "source_type": SourceType.RSS,
        "twitter": "@ForexLive",
    },
    {
        "name": "FXStreet News",
        "url": "https://www.fxstreet.com/rss/news",
        "tier": "MEDIUM",
        "source_type": SourceType.RSS,
        "twitter": "@FXStreet",
    },
    {
        "name": "Investing.com News",
        "url": "https://www.investing.com/rss/news_25.rss",
        "tier": "MEDIUM",
        "source_type": SourceType.RSS,
        "twitter": None,
    },
    {
        "name": "MarketWatch Top Stories",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "tier": "MEDIUM",
        "source_type": SourceType.RSS,
        "twitter": "@MarketWatch",
    },
    {
        "name": "WSJ Markets",
        "url": "https://feeds.wsj.com/wsj/xml/rss/3_7031.xml",
        "tier": "HIGH",
        "source_type": SourceType.RSS,
        "twitter": "@WSJmarkets",
    },
    {
        "name": "ZeroHedge",
        "url": "https://feeds.feedburner.com/zerohedge/feed",
        "tier": "MEDIUM",
        "source_type": SourceType.RSS,
        "twitter": "@zerohedge",
    },
]


def _is_relevant(text: str) -> bool:
    """Quick keyword-based relevance filter before sending to LLM."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in HIGH_IMPACT_EVENTS) or any(
        sym in text_lower for sym in ("gold", "xau", "nasdaq", "ndx", "yen", "dollar", "rate", "inflation")
    )


def _parse_pub_date(entry: feedparser.FeedParserDict) -> datetime | None:
    """Parse published date from feedparser entry."""
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return None


class RSSHeadlineMonitor:
    """
    Polls multiple RSS feeds concurrently.
    Dispatches relevant NewsHeadline items to the shared queue.
    """

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self._queue = queue
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "MacroPulse-Sentinel/1.0 RSS Monitor"},
        )
        log.info("rss_monitor.started", feeds=len(RSS_FEEDS))
        # Launch one task per feed
        async with asyncio.TaskGroup() as tg:
            for feed_cfg in RSS_FEEDS:
                tg.create_task(self._poll_feed_loop(feed_cfg))

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    async def _poll_feed_loop(self, feed_cfg: dict[str, Any]) -> None:
        """Infinite polling loop for a single feed with jitter."""
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        while True:
            # Jitter ±20% to avoid thundering herd
            jitter = random.uniform(0.8, 1.2)
            interval = settings.rss_poll_interval_seconds * jitter
            try:
                await self._poll_feed_once(feed_cfg)
            except Exception as exc:
                log.warning("rss_monitor.feed_error", feed=name, error=str(exc))
            await asyncio.sleep(interval)

    async def _poll_feed_once(self, feed_cfg: dict[str, Any]) -> None:
        url = feed_cfg["url"]
        name = feed_cfg["name"]
        try:
            async with self._session.get(url) as resp:  # type: ignore[union-attr]
                if resp.status != 200:
                    return
                content = await resp.read()
        except aiohttp.ClientError as exc:
            log.debug("rss_monitor.http_error", feed=name, error=str(exc))
            return

        parsed = feedparser.parse(content)
        for entry in parsed.entries[:20]:  # process only latest 20 entries
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link = getattr(entry, "link", "")
            full_text = f"{title} {summary}"

            if not _is_relevant(full_text):
                continue

            is_new = await dedup_cache.check_and_mark(full_text)
            if not is_new:
                continue

            h = hashlib.sha256(full_text.strip().encode()).hexdigest()
            headline = NewsHeadline(
                headline_id=h,
                title=title.strip(),
                summary=summary.strip() or None,
                url=link or None,
                source_name=name,
                source_type=feed_cfg["source_type"],
                twitter_handle=feed_cfg.get("twitter"),
                published_at=_parse_pub_date(entry),
                raw_text=full_text,
            )
            log.info(
                "rss_monitor.headline_dispatched",
                feed=name,
                title=title[:100],
            )
            await self._queue.put(headline)
