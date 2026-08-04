"""Manually trigger any scheduled job for testing.

Usage:
    railway run uv run run-jobs --job daily-snapshot
    railway run uv run run-jobs --job weekly-snapshot
    railway run uv run run-jobs --job daily-mentions
    railway run uv run run-jobs --job all
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

JOBS = ["daily-snapshot", "weekly-snapshot", "daily-mentions"]


async def _run(job: str, channel_id: str | None = None) -> None:
    from organic_social_agent.db import close_pool, create_tables, init_pool

    await init_pool()
    await create_tables()

    try:
        if job in ("daily-snapshot", "all"):
            logger.info("--- running: daily-snapshot ---")
            from organic_social_agent.slack.scheduler import _run_daily_snapshot
            await _run_daily_snapshot()

        if job in ("weekly-snapshot", "all"):
            logger.info("--- running: weekly-snapshot ---")
            from organic_social_agent.slack.scheduler import _run_weekly_post_snapshot
            await _run_weekly_post_snapshot()

        if job in ("daily-mentions", "all"):
            logger.info("--- running: daily-mentions ---")
            from organic_social_agent.slack.scheduler import _run_daily_mentions
            await _run_daily_mentions(channel_id=channel_id)
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manually trigger a scheduled job.")
    parser.add_argument(
        "--job",
        choices=JOBS + ["all"],
        required=True,
        help="Which job to run",
    )
    parser.add_argument(
        "--channel",
        metavar="CHANNEL_ID",
        default=None,
        help="Override the Slack channel for this run (e.g. a test channel). "
             "Omit to use the production channel from settings.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.job, channel_id=args.channel))
