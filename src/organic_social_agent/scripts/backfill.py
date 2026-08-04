"""One-off backfill — seeds daily_kpi_snapshot and post_snapshot with historical data.

Run this once after deployment to populate the database before the scheduled
jobs have had time to accumulate data organically.

Usage:
    uv run backfill                    # 30 days of KPIs + 90-day post snapshot
    uv run backfill --kpi-days 14      # shorter KPI window
    uv run backfill --skip-posts       # KPIs only
    uv run backfill --skip-kpis        # post snapshot only
    railway run backfill               # run against the Railway Postgres in prod

Meta API note:
  Daily KPIs are fetched as 30 individual 1-day windows (since=D, until=D+1).
  Meta rejects any account-level insights window longer than 30 days, so day-by-
  day is the only way to get historical daily granularity. Each call makes several
  parallel sub-requests internally; a 0.5s pause between days avoids rate-limiting.

  Posts are fetched in one call (last 90 days) and saved as a single weekly
  snapshot keyed to the current Monday. Per-post metrics reflect current values —
  Meta has no way to return what the numbers were at a specific past date.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta

from loguru import logger


async def _backfill_daily_kpis(days: int) -> None:
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.history import save_daily_kpi

    today = datetime.now(UTC).date()
    logger.info("backfilling daily KPIs for {} days ({} → {})",
                days, today - timedelta(days=days), today - timedelta(days=1))

    ok = failed = 0
    for i in range(days, 0, -1):
        day = today - timedelta(days=i)
        day_end = day + timedelta(days=1)
        try:
            kpis = await meta_insights.fetch_account_kpis(day, day_end)
            await save_daily_kpi(kpis, day)
            logger.info("  ✓ KPIs for {}: reach={} views={}", day, kpis.reach_total, kpis.views)
            ok += 1
        except Exception as exc:
            logger.warning("  ✗ KPIs for {}: {!r}", day, exc)
            failed += 1
        await asyncio.sleep(0.5)  # stay well under Meta rate limits

    logger.info("daily KPI backfill complete: {} saved, {} failed", ok, failed)


async def _backfill_post_snapshot(post_days: int) -> None:
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.history import save_post_snapshot

    today = datetime.now(UTC).date()
    since = today - timedelta(days=post_days)
    snapshot_week = today - timedelta(days=today.weekday())  # Monday of this week

    logger.info("backfilling post snapshot: {} posts from {} → {} (week {})",
                "up to 200", since, today, snapshot_week)
    try:
        posts = await meta_insights.fetch_recent_posts(since, today, max_posts=200)
        await save_post_snapshot(posts, snapshot_week)
        logger.info("post snapshot complete: {} posts saved for week {}", len(posts), snapshot_week)
    except Exception as exc:
        logger.error("post snapshot backfill failed: {!r}", exc)


async def _run(kpi_days: int, post_days: int, skip_kpis: bool, skip_posts: bool) -> None:
    from organic_social_agent.db import close_pool, create_tables, init_pool

    await init_pool()
    await create_tables()

    try:
        if not skip_kpis:
            await _backfill_daily_kpis(kpi_days)
        if not skip_posts:
            await _backfill_post_snapshot(post_days)
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill daily_kpi_snapshot and post_snapshot with historical data."
    )
    parser.add_argument(
        "--kpi-days", type=int, default=30,
        help="How many days of daily KPI history to fetch (default: 30, max: 30 — Meta API limit)",
    )
    parser.add_argument(
        "--post-days", type=int, default=90,
        help="How far back to fetch posts for the weekly snapshot (default: 90)",
    )
    parser.add_argument("--skip-kpis", action="store_true", help="Skip daily KPI backfill")
    parser.add_argument("--skip-posts", action="store_true", help="Skip post snapshot backfill")
    args = parser.parse_args()

    if args.kpi_days > 30:
        parser.error("--kpi-days cannot exceed 30: Meta API rejects account-level windows longer than 30 days")

    asyncio.run(_run(
        kpi_days=args.kpi_days,
        post_days=args.post_days,
        skip_kpis=args.skip_kpis,
        skip_posts=args.skip_posts,
    ))
