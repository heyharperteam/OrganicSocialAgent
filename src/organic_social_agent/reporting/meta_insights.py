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

from datetime import UTC, date, datetime

import httpx
import truststore
from loguru import logger

from organic_social_agent.reporting.schema import Kpis, PostPerformance
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


def _base() -> str:
    return f"{_GRAPH}/{settings.meta_api_version}"


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict | None:
    """GET a Graph edge, returning parsed JSON or None on HTTP error (logged)."""
    params = {**params, "access_token": settings.meta_access_token}
    try:
        r = await client.get(f"{_base()}{path}", params=params)
        r.raise_for_status()
        return r.json()
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


async def fetch_recent_posts(since: date, until: date, *, max_posts: int = 50) -> list[PostPerformance]:
    """Recent posts published in [since, until) with per-post insights."""
    s, u = _epoch(since), _epoch(until)
    posts: list[PostPerformance] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        # page through /media until we have enough or run out
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
                r = await client.get(next_url)  # absolute URL, token already embedded
                r.raise_for_status()
                page = r.json()
            except httpx.HTTPStatusError:
                break

        for m in media[:max_posts]:
            post = PostPerformance(
                media_id=m["id"],
                caption=m.get("caption", "") or "",
                media_type=m.get("media_type", "") or "",
                product_type=m.get("media_product_type", "") or "",
                permalink=m.get("permalink", "") or "",
                timestamp=m.get("timestamp", "") or "",
                likes=int(m.get("like_count") or 0),
                comments=int(m.get("comments_count") or 0),
            )
            ins = await _get(
                client,
                f"/{m['id']}/insights",
                {"metric": ",".join(_MEDIA_METRICS)},
            )
            for row in (ins or {}).get("data", []):
                name = row.get("name")
                vals = row.get("values") or []
                value = int(vals[0].get("value") or 0) if vals else 0
                if name == "reach":
                    post.reach = value
                elif name == "saved":
                    post.saved = value
                elif name == "shares":
                    post.shares = value
                elif name == "views":
                    post.views = value
                elif name == "total_interactions":
                    post.total_interactions = value
            posts.append(post)

    logger.info("fetched {} post(s) in window", len(posts))
    return posts
