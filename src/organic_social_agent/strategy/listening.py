"""Competitor Instagram metrics via Meta Business Discovery API.

Reads public data (followers, recent posts, like/comment counts) for a
list of competitor IG handles. Uses the same Meta token as the reporting
engine — no additional credentials needed.

Limitation: Business Discovery only exposes public metrics. Reach, saves,
and shares are private to each account and are never returned by the API.
Only Business and Creator accounts are accessible; personal accounts error.
"""

from __future__ import annotations

import asyncio
import json as _json
import urllib.request
from urllib.parse import quote

import truststore
from loguru import logger

from organic_social_agent.settings import settings

truststore.inject_into_ssl()


def _fetch_one_sync(handle: str) -> bytes:
    """Synchronous inner fetch — returns raw response bytes (including error bodies).

    Uses urllib.request (not httpx) so the URL reaches the socket without
    re-encoding the {} chars that Meta requires as literal in the fields string.
    """
    import urllib.error

    # Correct Business Discovery format: username goes inside the fields expression
    # as business_discovery.username(handle){subfields} — NOT as &username= param.
    fields = (
        f"business_discovery.username({handle})"
        "{followers_count,media_count,name,"
        "media.limit(12){caption,media_type,like_count,comments_count,timestamp}}"
    )
    base = f"https://graph.facebook.com/{settings.meta_api_version}"
    url = (
        f"{base}/{settings.meta_ig_user_id}"
        f"?fields={quote(fields, safe='(){}.,_-')}"
        f"&access_token={quote(settings.meta_access_token, safe='')}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:
        return exc.read()


async def _fetch_one(handle: str) -> dict | None:
    """Fetch public profile + recent posts for one competitor handle."""
    loop = asyncio.get_event_loop()
    try:
        raw = await loop.run_in_executor(None, _fetch_one_sync, handle)
        data = _json.loads(raw)
    except Exception as exc:
        logger.warning("Business Discovery request error for @{}: {}", handle, exc)
        return None
    if "error" in data:
        logger.warning(
            "Business Discovery failed for @{}: {}", handle,
            data["error"].get("message", data["error"])
        )
        return None
    bd = data.get("business_discovery")
    if bd:
        logger.info(
            "Business Discovery OK for @{}: {} followers, {} posts",
            handle, bd.get("followers_count"), bd.get("media_count"),
        )
    else:
        logger.warning("Business Discovery: no 'business_discovery' key for @{}. Full response: {}", handle, data)
    return bd


async def fetch_competitor_context() -> str:
    """Fetch public metrics for all competitor handles concurrently.

    Handles are read from the DB config table (key: competitor_ig_handles) at call
    time, falling back to the settings value so the default still works without a DB.

    Returns a plain-text context block ready to append to Claude's prompt.
    Returns an empty string if no handles are configured or all fetches fail.
    """
    from organic_social_agent.db_config import get_config

    raw = await get_config("competitor_ig_handles", settings.competitor_ig_handles)
    handles = [h.strip() for h in raw.split(",") if h.strip()]

    if not handles or not settings.meta_access_token or not settings.meta_ig_user_id:
        return ""

    results = await asyncio.gather(
        *[_fetch_one(h) for h in handles],
        return_exceptions=True,
    )

    lines = ["## Competitor Instagram accounts (public data — likes/comments only, no reach/saves)"]
    any_data = False

    for handle, result in zip(handles, results):
        if isinstance(result, Exception):
            logger.warning("competitor fetch error for @{}: {!r}", handle, result)
            continue
        if result is None:
            continue

        any_data = True
        followers = result.get("followers_count", 0)
        media_count = result.get("media_count", 0)
        name = result.get("name", handle)

        lines.append(f"\n@{handle} ({name}) — {followers:,} followers · {media_count:,} posts")

        posts = result.get("media", {}).get("data", [])
        if posts:
            for post in posts[:8]:
                caption = (post.get("caption") or "").replace("\n", " ")[:80]
                media_type = post.get("media_type", "")
                likes = post.get("like_count", 0)
                comments = post.get("comments_count", 0)
                ts = (post.get("timestamp") or "")[:10]
                lines.append(
                    f'  • [{media_type}] "{caption}" — {likes:,} likes · {comments} comments ({ts})'
                )

    if not any_data:
        return ""

    return "\n".join(lines)
