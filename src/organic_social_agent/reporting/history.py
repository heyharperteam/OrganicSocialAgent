"""Long-term KPI history — what beats Instagram's ~90-day retention.

Every report run appends a KpiSnapshot so trends can be compared across months
and quarters long after Instagram itself has forgotten the daily numbers.

Storage: Postgres (`kpi_snapshot` table, managed by db.py).
If DATABASE_URL is not set (local dev without Postgres), persistence is skipped
with a warning — the app still runs, deltas just won't have a stored prior window.
Use `report --no-persist` explicitly when running locally without a database.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from loguru import logger

from organic_social_agent.reporting.schema import KpiSnapshot, Kpis, PostPerformance


async def save_snapshot(snapshot: KpiSnapshot) -> None:
    """Insert one snapshot into Postgres."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        logger.warning("no DB pool — skipping KPI snapshot persist")
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO kpi_snapshot (platform, since, until, captured_at, data)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (platform, since, until)
            DO UPDATE SET captured_at = EXCLUDED.captured_at,
                          data        = EXCLUDED.data
            """,
            snapshot.kpis.platform,
            snapshot.kpis.since,
            snapshot.kpis.until,
            datetime.fromisoformat(snapshot.captured_at),
            snapshot.model_dump_json(),
        )
    logger.info(
        "saved KPI snapshot {}→{} ({})",
        snapshot.kpis.since, snapshot.kpis.until, snapshot.kpis.platform,
    )


async def load_snapshots(platform: str | None = None) -> list[KpiSnapshot]:
    """All stored snapshots (optionally one platform), oldest first."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        return []

    if platform:
        query = "SELECT data FROM kpi_snapshot WHERE platform = $1 ORDER BY since, captured_at"
        args: list = [platform]
    else:
        query = "SELECT data FROM kpi_snapshot ORDER BY since, captured_at"
        args = []

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    return [KpiSnapshot.model_validate_json(row["data"]) for row in rows]


async def oldest_since(platform: str = "instagram") -> date | None:
    """Earliest `since` date stored for the given platform.

    Returns None when the DB is unavailable or empty — callers must handle this
    gracefully (skip the cap, or assume no history).
    """
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT MIN(since) AS oldest FROM kpi_snapshot WHERE platform = $1",
            platform,
        )
    return row["oldest"] if row and row["oldest"] else None


async def save_daily_kpi(kpis: Kpis, snapshot_date: date) -> None:
    """Upsert one day's account KPIs into daily_kpi_snapshot."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        logger.warning("no DB pool — skipping daily KPI persist")
        return

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO daily_kpi_snapshot
                (platform, snapshot_date, captured_at, views, reach_total,
                 reach_follower, reach_non_follower, saves, shares,
                 profile_views, website_clicks)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (platform, snapshot_date)
            DO UPDATE SET
                captured_at        = EXCLUDED.captured_at,
                views              = EXCLUDED.views,
                reach_total        = EXCLUDED.reach_total,
                reach_follower     = EXCLUDED.reach_follower,
                reach_non_follower = EXCLUDED.reach_non_follower,
                saves              = EXCLUDED.saves,
                shares             = EXCLUDED.shares,
                profile_views      = EXCLUDED.profile_views,
                website_clicks     = EXCLUDED.website_clicks
            """,
            kpis.platform, snapshot_date, now,
            kpis.views, kpis.reach_total, kpis.reach_follower, kpis.reach_non_follower,
            kpis.saves, kpis.shares, kpis.profile_views, kpis.website_clicks,
        )
    logger.info("daily KPI snapshot saved for {}", snapshot_date)


async def save_story_snapshot(stories: list[PostPerformance], snapshot_date: date) -> None:
    """Upsert today's active stories into story_snapshot."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        logger.warning("no DB pool — skipping story snapshot persist")
        return
    if not stories:
        logger.info("story snapshot: 0 active stories on {}, nothing to save", snapshot_date)
        return

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO story_snapshot
                (snapshot_date, captured_at, media_id, caption, permalink, post_ts,
                 impressions, reach, taps_forward, taps_back, exits, replies)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (snapshot_date, media_id)
            DO UPDATE SET
                captured_at  = EXCLUDED.captured_at,
                impressions  = EXCLUDED.impressions,
                reach        = EXCLUDED.reach,
                taps_forward = EXCLUDED.taps_forward,
                taps_back    = EXCLUDED.taps_back,
                exits        = EXCLUDED.exits,
                replies      = EXCLUDED.replies
            """,
            [
                (snapshot_date, now, s.media_id, s.caption, s.permalink, s.timestamp,
                 s.impressions, s.reach, s.taps_forward, s.taps_back, s.exits, s.story_replies)
                for s in stories
            ],
        )
    logger.info("story snapshot saved: {} stories for {}", len(stories), snapshot_date)


async def load_story_snapshots(since: date, until: date) -> list[dict]:
    """All story snapshots in [since, until), newest first within each story."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (media_id)
                snapshot_date, media_id, caption, permalink, post_ts,
                impressions, reach, taps_forward, taps_back, exits, replies
            FROM story_snapshot
            WHERE snapshot_date >= $1 AND snapshot_date < $2
            ORDER BY media_id, snapshot_date DESC
            """,
            since, until,
        )
    return [dict(r) for r in rows]


async def save_post_snapshot(posts: list[PostPerformance], snapshot_week: date) -> None:
    """Upsert this week's per-post metrics into post_snapshot."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        logger.warning("no DB pool — skipping post snapshot persist")
        return
    if not posts:
        logger.info("post snapshot: no posts to save for week {}", snapshot_week)
        return

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO post_snapshot
                (snapshot_week, captured_at, media_id, caption, media_type, product_type,
                 permalink, post_ts, reach, views, likes, comments, saved, shares,
                 total_interactions, avg_watch_time_ms, completion_rate)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT (snapshot_week, media_id)
            DO UPDATE SET
                captured_at        = EXCLUDED.captured_at,
                reach              = EXCLUDED.reach,
                views              = EXCLUDED.views,
                likes              = EXCLUDED.likes,
                comments           = EXCLUDED.comments,
                saved              = EXCLUDED.saved,
                shares             = EXCLUDED.shares,
                total_interactions = EXCLUDED.total_interactions,
                avg_watch_time_ms  = EXCLUDED.avg_watch_time_ms,
                completion_rate    = EXCLUDED.completion_rate
            """,
            [
                (snapshot_week, now, p.media_id, p.caption, p.media_type, p.product_type,
                 p.permalink, p.timestamp, p.reach, p.views, p.likes, p.comments,
                 p.saved, p.shares, p.total_interactions, p.avg_watch_time_ms, p.completion_rate)
                for p in posts
            ],
        )
    logger.info("post snapshot saved: {} posts for week {}", len(posts), snapshot_week)


async def load_post_snapshots(since: date, until: date) -> list[dict]:
    """Most recent snapshot per post within [since, until)."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (media_id)
                snapshot_week, media_id, caption, media_type, product_type,
                permalink, post_ts, reach, views, likes, comments, saved, shares,
                total_interactions, avg_watch_time_ms, completion_rate
            FROM post_snapshot
            WHERE snapshot_week >= $1 AND snapshot_week < $2
            ORDER BY media_id, snapshot_week DESC
            """,
            since, until,
        )
    return [dict(r) for r in rows]


async def latest_before(since_exclusive: date, platform: str = "instagram") -> Kpis | None:
    """Most recent snapshot whose window ends on/before `since_exclusive`.

    Used to fill in prior-window deltas when the Meta API can't reach far enough back.
    """
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT data FROM kpi_snapshot
            WHERE platform = $1 AND until <= $2
            ORDER BY since DESC, captured_at DESC
            LIMIT 1
            """,
            platform,
            since_exclusive,
        )

    if not row:
        return None
    return KpiSnapshot.model_validate_json(row["data"]).kpis
