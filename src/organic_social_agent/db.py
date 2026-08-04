"""Postgres connection pool — shared across the app.

Initialised on FastAPI startup if DATABASE_URL is set. If not set (local dev
without a Postgres instance), history persistence is skipped gracefully and
the app still runs — use `report --no-persist` or set a local DATABASE_URL.

Table DDL is applied at startup via create_tables(); no migration tool needed
for the single table we manage here.
"""

from __future__ import annotations

import asyncpg
from loguru import logger

from organic_social_agent.settings import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    if not settings.database_url:
        logger.warning("DATABASE_URL not set — KPI history persistence disabled")
        return
    _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    logger.info("Postgres pool ready")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    return _pool


async def create_tables() -> None:
    """Idempotent DDL — safe to run on every startup."""
    pool = get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kpi_snapshot (
                id           BIGSERIAL PRIMARY KEY,
                platform     TEXT        NOT NULL,
                since        DATE        NOT NULL,
                until        DATE        NOT NULL,
                captured_at  TIMESTAMPTZ NOT NULL,
                data         JSONB       NOT NULL,
                UNIQUE (platform, since, until)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_kpi_snapshot (
                id                  BIGSERIAL    PRIMARY KEY,
                platform            TEXT         NOT NULL DEFAULT 'instagram',
                snapshot_date       DATE         NOT NULL,
                captured_at         TIMESTAMPTZ  NOT NULL,
                views               BIGINT       NOT NULL DEFAULT 0,
                reach_total         BIGINT       NOT NULL DEFAULT 0,
                reach_follower      BIGINT       NOT NULL DEFAULT 0,
                reach_non_follower  BIGINT       NOT NULL DEFAULT 0,
                saves               BIGINT       NOT NULL DEFAULT 0,
                shares              BIGINT       NOT NULL DEFAULT 0,
                profile_views       BIGINT       NOT NULL DEFAULT 0,
                website_clicks      BIGINT       NOT NULL DEFAULT 0,
                UNIQUE (platform, snapshot_date)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS story_snapshot (
                id            BIGSERIAL    PRIMARY KEY,
                snapshot_date DATE         NOT NULL,
                captured_at   TIMESTAMPTZ  NOT NULL,
                media_id      TEXT         NOT NULL,
                caption       TEXT         NOT NULL DEFAULT '',
                permalink     TEXT         NOT NULL DEFAULT '',
                post_ts       TEXT         NOT NULL DEFAULT '',
                impressions   INT          NOT NULL DEFAULT 0,
                reach         INT          NOT NULL DEFAULT 0,
                taps_forward  INT          NOT NULL DEFAULT 0,
                taps_back     INT          NOT NULL DEFAULT 0,
                exits         INT          NOT NULL DEFAULT 0,
                replies       INT          NOT NULL DEFAULT 0,
                UNIQUE (snapshot_date, media_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS post_snapshot (
                id                  BIGSERIAL    PRIMARY KEY,
                snapshot_week       DATE         NOT NULL,
                captured_at         TIMESTAMPTZ  NOT NULL,
                media_id            TEXT         NOT NULL,
                caption             TEXT         NOT NULL DEFAULT '',
                media_type          TEXT         NOT NULL DEFAULT '',
                product_type        TEXT         NOT NULL DEFAULT '',
                permalink           TEXT         NOT NULL DEFAULT '',
                post_ts             TEXT         NOT NULL DEFAULT '',
                reach               INT          NOT NULL DEFAULT 0,
                views               INT          NOT NULL DEFAULT 0,
                likes               INT          NOT NULL DEFAULT 0,
                comments            INT          NOT NULL DEFAULT 0,
                saved               INT          NOT NULL DEFAULT 0,
                shares              INT          NOT NULL DEFAULT 0,
                total_interactions  INT          NOT NULL DEFAULT 0,
                avg_watch_time_ms   INT          NOT NULL DEFAULT 0,
                completion_rate     FLOAT        NOT NULL DEFAULT 0.0,
                UNIQUE (snapshot_week, media_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key        TEXT        PRIMARY KEY,
                value      TEXT        NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL
            );
        """)
    logger.info("kpi_snapshot, daily_kpi_snapshot, story_snapshot, post_snapshot, config tables ready")
