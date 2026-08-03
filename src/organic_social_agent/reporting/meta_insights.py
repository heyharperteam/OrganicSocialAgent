"""Meta / Instagram Insights — read the organic KPIs (READ-ONLY).

Maps Hey Harper's 4 tracked KPIs onto Instagram Graph Insights:

    KPI 1  non-follower reach %   ← account `reach`, breakdown=follow_type
    KPI 2  total views            ← account `views`
    KPI 3  saves + shares / reach ← account `saves` + `shares` over `reach`
    KPI 4  profile visits→clicks   ← account `profile_views` + `website_clicks`

Plus per-post metrics (`fetch_recent_posts`) so the report can flag standouts.

Everything here hits the IG Business account (settings.meta_ig_user_id) with the
existing read token — no publish scope, so this ships before any App Review.

Robustness: the Graph metric catalogue shifts between API versions, so each
metric is fetched in an isolated call — one unsupported metric logs a warning and
yields 0 rather than failing the whole pull. Run `--probe` (see report.py / the
scratch diagnostic) to see exactly what the live account returns.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import httpx
import truststore
from loguru import logger

from organic_social_agent.reporting.schema import AudienceDemographics, Kpis, PostPerformance
from organic_social_agent.settings import settings

truststore.inject_into_ssl()

_GRAPH = "https://graph.facebook.com"

# Account-level metrics that must be queried as an aggregate total over the
# window (metric_type=total_value). Split out because breakdown/period rules
# differ from time-series metrics.
_TOTAL_VALUE_METRICS = [
    "reach",
    "views",
    "profile_views",
    "website_clicks",
    "total_interactions",
    "saves",
    "shares",
    "likes",
    "comments",
]

_MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
_MEDIA_METRICS = ["reach", "saved", "shares", "total_interactions", "views"]
_REEL_METRICS = "ig_reels_avg_watch_time,ig_reels_video_view_full_plays"
_STORY_METRICS = "impressions,reach,taps_forward,taps_back,exits,replies"


def _base() -> str:
    return f"{_GRAPH}/{settings.meta_api_version}"


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict | None:
    """GET a Graph edge, returning parsed JSON or None on any error (logged)."""
    params = {**params, "access_token": settings.meta_access_token}
    try:
        r = await client.get(f"{_base()}{path}", params=params)
        r.raise_for_status()
        data = r.json()
        # Meta sometimes returns HTTP 200 with an error object instead of a 4xx
        if "error" in data:
            logger.warning(
                "Graph {} API error: {}", path,
                data["error"].get("message", data["error"]),
            )
            return None
        return data
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300]
        logger.warning("Graph {} failed ({}): {}", path, exc.response.status_code, body)
        return None


async def _account_total(
    client: httpx.AsyncClient, metric: str, since: int, until: int, breakdown: str | None = None
) -> dict | None:
    """Fetch one account-level total_value metric over [since, until).

    Returns the metric's `total_value` object (with optional breakdowns), or None.
    """
    params: dict = {
        "metric": metric,
        "metric_type": "total_value",
        "period": "day",
        "since": since,
        "until": until,
    }
    if breakdown:
        params["breakdown"] = breakdown
    data = await _get(client, f"/{settings.meta_ig_user_id}/insights", params)
    if not data or not data.get("data"):
        return None
    return data["data"][0].get("total_value")


def _split_reach_by_follow_type(total_value: dict | None) -> tuple[int, int, int]:
    """→ (reach_total, reach_follower, reach_non_follower) from a reach total_value
    that carries a follow_type breakdown; falls back to total-only if absent."""
    if not total_value:
        return 0, 0, 0
    total = int(total_value.get("value") or 0)
    follower = non_follower = 0
    for bd in total_value.get("breakdowns", []) or []:
        # dimension_keys tells us which breakdown this is; results carry the split
        for res in bd.get("results", []) or []:
            keys = [str(k).lower() for k in (res.get("dimension_values") or [])]
            val = int(res.get("value") or 0)
            if "follower" in keys:
                follower = val
            elif "non_follower" in keys or "non-follower" in keys:
                non_follower = val
    if follower or non_follower:
        return total or (follower + non_follower), follower, non_follower
    return total, 0, 0


async def fetch_account_kpis(since: date, until: date) -> Kpis:
    """Pull the 4 KPIs' raw components for the window [since, until)."""
    s, u = _epoch(since), _epoch(until)
    kpis = Kpis(platform="instagram", since=since, until=until)

    async with httpx.AsyncClient(timeout=60.0) as client:
        # KPI 1 — reach split by follower / non-follower
        reach_tv = await _account_total(client, "reach", s, u, breakdown="follow_type")
        r_total, r_fol, r_non = _split_reach_by_follow_type(reach_tv)
        if not r_total:  # breakdown unsupported → plain reach
            plain = await _account_total(client, "reach", s, u)
            r_total = int((plain or {}).get("value") or 0)
        kpis.reach_total, kpis.reach_follower, kpis.reach_non_follower = r_total, r_fol, r_non

        # remaining scalar totals
        async def scalar(metric: str) -> int:
            tv = await _account_total(client, metric, s, u)
            return int((tv or {}).get("value") or 0)

        kpis.views = await scalar("views")
        kpis.profile_views = await scalar("profile_views")
        kpis.website_clicks = await scalar("website_clicks")
        kpis.saves = await scalar("saves")
        kpis.shares = await scalar("shares")

    logger.info(
        "KPIs {}→{}: reach={} (non-follower {:.0f}%), views={}, saves+shares={}, "
        "profile_views={}, website_clicks={}",
        since, until, kpis.reach_total, kpis.non_follower_reach_pct, kpis.views,
        kpis.saves + kpis.shares, kpis.profile_views, kpis.website_clicks,
    )
    return kpis


def _parse_insights_values(data: dict | None) -> dict[str, float]:
    """Extract {metric_name: value} from an insights response.

    Handles both formats Meta uses:
    - time-series:  {"values": [{"value": 123, ...}]}
    - aggregate:    {"total_value": {"value": 123}}
    """
    out: dict[str, float] = {}
    for row in (data or {}).get("data", []):
        name = row.get("name")
        if not name:
            continue
        vals = row.get("values") or []
        if vals:
            out[name] = float(vals[0].get("value") or 0)
        elif "total_value" in row:
            tv = row.get("total_value") or {}
            if "value" in tv:
                out[name] = float(tv["value"] or 0)
    return out


def _parse_follower_breakdown(data: dict | None) -> tuple[int, int]:
    """Return (reach_follower, reach_non_follower) from a breakdown insights call."""
    follower = non_follower = 0
    for row in (data or {}).get("data", []):
        tv = row.get("total_value") or {}
        for bd in tv.get("breakdowns", []) or []:
            for res in bd.get("results", []) or []:
                keys = [str(k).lower() for k in (res.get("dimension_values") or [])]
                val = int(res.get("value") or 0)
                if "follower" in keys and "non" not in " ".join(keys):
                    follower = val
                elif "non_follower" in keys or "non-follower" in " ".join(keys):
                    non_follower = val
    return follower, non_follower


async def _fetch_post_metrics(client: httpx.AsyncClient, m: dict) -> PostPerformance:
    """Fetch all available metrics for a single media item concurrently."""
    product_type = (m.get("media_product_type", "") or "").upper()
    post = PostPerformance(
        media_id=m["id"],
        caption=m.get("caption", "") or "",
        media_type=m.get("media_type", "") or "",
        product_type=product_type,
        permalink=m.get("permalink", "") or "",
        timestamp=m.get("timestamp", "") or "",
        likes=int(m.get("like_count") or 0),
        comments=int(m.get("comments_count") or 0),
    )

    is_reel = product_type == "REELS"
    is_story = product_type == "STORY"

    # Fan out: base metrics + follower breakdown always; type-specific when applicable
    tasks = [
        _get(client, f"/{m['id']}/insights", {"metric": ",".join(_MEDIA_METRICS)}),
        _get(client, f"/{m['id']}/insights", {
            "metric": "reach",
            "breakdown": "follower_type",
            "metric_type": "total_value",
        }),
    ]
    if is_reel:
        tasks.append(_get(client, f"/{m['id']}/insights", {
            "metric": _REEL_METRICS,
            "metric_type": "total_value",
        }))
    elif is_story:
        tasks.append(_get(client, f"/{m['id']}/insights", {"metric": _STORY_METRICS}))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Base metrics
    base = results[0] if not isinstance(results[0], Exception) else None
    for name, val in _parse_insights_values(base).items():
        if name == "reach":
            post.reach = int(val)
        elif name == "saved":
            post.saved = int(val)
        elif name == "shares":
            post.shares = int(val)
        elif name == "views":
            post.views = int(val)
        elif name == "total_interactions":
            post.total_interactions = int(val)

    # Follower breakdown
    bd = results[1] if not isinstance(results[1], Exception) else None
    post.reach_follower, post.reach_non_follower = _parse_follower_breakdown(bd)
    if not post.reach_follower and not post.reach_non_follower:
        logger.debug("follower breakdown empty for {} — raw: {!r}", m["id"], bd)

    # Type-specific
    if len(results) > 2 and not isinstance(results[2], Exception):
        extras = _parse_insights_values(results[2])
        if is_reel:
            post.avg_watch_time_ms = int(extras.get("ig_reels_avg_watch_time", 0))
            full_plays = int(extras.get("ig_reels_video_view_full_plays", 0))
            if full_plays and post.views:
                post.completion_rate = min(full_plays / post.views, 1.0)
            elif full_plays:
                post.completion_rate = -1.0
            if not post.avg_watch_time_ms and not full_plays:
                logger.debug("reel metrics empty for {} — raw: {!r}", m["id"], results[2])
        elif is_story:
            post.impressions = int(extras.get("impressions", 0))
            post.taps_forward = int(extras.get("taps_forward", 0))
            post.taps_back = int(extras.get("taps_back", 0))
            post.exits = int(extras.get("exits", 0))
            post.story_replies = int(extras.get("replies", 0))
            if not post.impressions:
                logger.debug("story metrics empty for {} — raw: {!r}", m["id"], results[2])
    elif len(results) > 2 and isinstance(results[2], Exception):
        logger.warning("type-specific metrics failed for {}: {!r}", m["id"], results[2])

    return post


async def fetch_recent_posts(since: date, until: date, *, max_posts: int = 50) -> list[PostPerformance]:
    """Recent posts published in [since, until) with per-post insights."""
    s, u = _epoch(since), _epoch(until)

    async with httpx.AsyncClient(timeout=60.0) as client:
        media: list[dict] = []
        page = await _get(
            client,
            f"/{settings.meta_ig_user_id}/media",
            {"fields": _MEDIA_FIELDS, "since": s, "until": u, "limit": 50},
        )
        while page and len(media) < max_posts:
            media.extend(page.get("data", []))
            next_url = (page.get("paging") or {}).get("next")
            if not next_url:
                break
            try:
                r = await client.get(next_url)
                r.raise_for_status()
                page = r.json()
            except httpx.HTTPStatusError:
                break

        posts = await asyncio.gather(
            *[_fetch_post_metrics(client, m) for m in media[:max_posts]],
            return_exceptions=True,
        )

    valid = [p for p in posts if isinstance(p, PostPerformance)]
    logger.info("fetched {} post(s) in window", len(valid))
    return valid


async def fetch_recent_stories() -> list[PostPerformance]:
    """Currently-active stories (last 24 h) with story-specific insights.

    Stories expire after 24 hours; call this at query time to capture live data.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        page = await _get(
            client,
            f"/{settings.meta_ig_user_id}/stories",
            {"fields": _MEDIA_FIELDS},
        )
        if not page:
            return []
        stories_raw = page.get("data", [])

        # For stories, set product_type to STORY so _fetch_post_metrics fetches the right metrics
        for s in stories_raw:
            s.setdefault("media_product_type", "STORY")

        results = await asyncio.gather(
            *[_fetch_post_metrics(client, s) for s in stories_raw],
            return_exceptions=True,
        )

    valid = [p for p in results if isinstance(p, PostPerformance)]
    logger.info("fetched {} active story/stories", len(valid))
    return valid


async def fetch_audience_demographics() -> AudienceDemographics:
    """Fetch current follower demographics: age/gender, top countries, top cities."""

    def _extract(resp: dict | None) -> dict[str, int]:
        if not isinstance(resp, dict) or not resp.get("data"):
            return {}
        row = resp["data"][0]
        vals = row.get("values") or []
        raw = vals[0].get("value") if vals else {}
        return {k: int(v) for k, v in (raw or {}).items()} if isinstance(raw, dict) else {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        gender_age, countries, cities = await asyncio.gather(
            _get(client, f"/{settings.meta_ig_user_id}/insights",
                 {"metric": "audience_gender_age", "period": "lifetime"}),
            _get(client, f"/{settings.meta_ig_user_id}/insights",
                 {"metric": "audience_country", "period": "lifetime"}),
            _get(client, f"/{settings.meta_ig_user_id}/insights",
                 {"metric": "audience_city", "period": "lifetime"}),
            return_exceptions=True,
        )

    demo = AudienceDemographics(
        gender_age=_extract(gender_age if not isinstance(gender_age, Exception) else None),
        top_countries=_extract(countries if not isinstance(countries, Exception) else None),
        top_cities=_extract(cities if not isinstance(cities, Exception) else None),
    )
    if not demo.gender_age:
        logger.warning(
            "audience demographics empty — gender_age raw: {!r}, countries raw: {!r}",
            gender_age, countries,
        )
    else:
        logger.info("demographics OK — {} age/gender segments", len(demo.gender_age))
    return demo
