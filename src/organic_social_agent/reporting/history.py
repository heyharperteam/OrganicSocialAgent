"""Long-term KPI history — what beats Instagram's ~90-day retention.

Every report run appends a KpiSnapshot so trends can be compared across months
and quarters long after Instagram itself has forgotten the daily numbers.

Storage: Postgres (`kpi_snapshot` table, managed by db.py).
If DATABASE_URL is not set (local dev without Postgres), persistence is skipped
with a warning — the app still runs, deltas just won't have a stored prior window.
Use `report --no-persist` explicitly when running locally without a database.
"""

from __future__ import annotations

from datetime import date, datetime

from loguru import logger

from organic_social_agent.reporting.schema import KpiSnapshot, Kpis


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
