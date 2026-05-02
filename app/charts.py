from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.models import utcnow

_FEED_URL = "https://itunes.apple.com/us/rss/topaudiobooks/limit=10/json"
_CACHE_TTL = timedelta(hours=12)

# Two independent module-level caches with the same TTL:
# _cache holds the raw iTunes chart (title/author/cover only).
# _enriched_cache holds the same data plus a source_id resolved via Listenarr search.
# Keeping them separate means a cold enrichment pass doesn't invalidate the raw feed.
_cache: list[dict[str, str]] = []
_cache_at: datetime | None = None

_enriched_cache: list[dict[str, str]] = []
_enriched_at: datetime | None = None

SearchFn = Callable[[str], Coroutine[Any, Any, list[dict[str, str]]]]


async def get_top_audiobooks() -> list[dict[str, str]]:
    """Fetch the iTunes top-audiobooks RSS feed and return normalized entries.

    On network or parse failure, the stale cache is returned silently rather than
    surfacing an error to the user. Returns [] only on the very first call if the
    feed has never loaded. Cover URLs are upscaled from 170x170 to 300x300.
    """
    global _cache, _cache_at
    now = utcnow()
    if _cache and _cache_at and (now - _cache_at) < _CACHE_TTL:
        return _cache
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(_FEED_URL)
            response.raise_for_status()
            data = response.json()
        results = []
        for entry in data.get("feed", {}).get("entry", []):
            title = entry.get("im:name", {}).get("label", "")
            author = entry.get("im:artist", {}).get("label", "")
            images = entry.get("im:image", [])
            cover = images[-1]["label"].replace("170x170bb", "300x300bb") if images else ""
            results.append({"title": title, "author": author, "cover_url": cover})
        _cache = results
        _cache_at = now
    except Exception:
        pass  # serve stale cache on error; returns [] on first failure
    return _cache


async def get_enriched_top_audiobooks(search_fn: SearchFn) -> list[dict[str, str]]:
    """Return chart books enriched with a Listenarr source_id via concurrent searches.
    Cached on the same TTL as the raw chart feed."""
    global _enriched_cache, _enriched_at
    now = utcnow()
    if _enriched_cache and _enriched_at and (now - _enriched_at) < _CACHE_TTL:
        return _enriched_cache

    books = await get_top_audiobooks()
    if not books:
        return []

    async def _enrich(book: dict[str, str]) -> dict[str, str]:
        # Failures are swallowed per-book so one bad search doesn't block the rest.
        try:
            results = await search_fn(f"{book['title']} {book['author']}")
            if results:
                return {**book, "source_id": results[0]["source_id"]}
        except Exception:
            pass
        return book

    enriched = await asyncio.gather(*[_enrich(b) for b in books])
    _enriched_cache = list(enriched)
    _enriched_at = now
    return _enriched_cache
