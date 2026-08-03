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
    logger.info("kpi_snapshot table ready")
