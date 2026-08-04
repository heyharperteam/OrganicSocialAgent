"""Runtime configuration stored in the `config` Postgres table.

Key-value store for client-facing knobs that must be editable from Slack
without a redeploy — competitor handles, report cadence, etc.

Falls back gracefully when the DB pool is unavailable (local dev without DB).
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger


async def get_config(key: str, default: str = "") -> str:
    """Read one config value. Returns default if key is absent or DB unavailable."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        return default

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key = $1", key)
    return row["value"] if row else default


async def set_config(key: str, value: str) -> None:
    """Upsert one config value."""
    from organic_social_agent.db import get_pool

    pool = get_pool()
    if not pool:
        logger.warning("no DB pool — cannot persist config key {!r}", key)
        return

    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO config (key, value, updated_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """,
            key, value, now,
        )
    logger.info("config updated: {} = {!r}", key, value)
