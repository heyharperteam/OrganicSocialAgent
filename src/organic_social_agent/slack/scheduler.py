"""APScheduler jobs — automated weekly and monthly report drops.

Runs inside the FastAPI process on its asyncio loop. Both jobs build the report
and post it to #social-media-reporting.

Schedule (runtime-config defaults, will move to Postgres config table):
  Weekly  — every Monday at 09:00 local time
  Monthly — 1st of each month at 09:00 local time
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import truststore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

truststore.inject_into_ssl()

_scheduler = AsyncIOScheduler()


async def _run_report(label: str, since: date, until: date) -> None:
    from organic_social_agent.reporting.report import build_report, post_to_slack

    try:
        logger.info("scheduled {} report: {} → {}", label, since, until)
        report = await build_report(since, until, label=label, persist=True)
        posted = post_to_slack(report)
        if not posted:
            logger.warning("scheduled {} report built but Slack delivery skipped (no token?)", label)
    except Exception as exc:
        logger.error("scheduled {} report failed: {!r}", label, exc)


def _weekly() -> None:
    until = date.today()
    since = until - timedelta(days=7)
    asyncio.create_task(_run_report("Weekly", since, until))


def _monthly() -> None:
    until = date.today()
    since = until - timedelta(days=30)
    asyncio.create_task(_run_report("Monthly", since, until))


async def _run_daily_snapshot() -> None:
    """Save daily KPIs (yesterday's 1-day window) and today's active stories."""
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.history import save_daily_kpi, save_story_snapshot

    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    try:
        kpis = await meta_insights.fetch_account_kpis(yesterday, today)
        await save_daily_kpi(kpis, today)
    except Exception as exc:
        logger.error("daily KPI snapshot failed: {!r}", exc)

    try:
        stories = await meta_insights.fetch_recent_stories()
        await save_story_snapshot(stories, today)
    except Exception as exc:
        logger.error("daily story snapshot failed: {!r}", exc)


async def _run_weekly_post_snapshot() -> None:
    """Save per-post metrics for all posts in the last 90 days."""
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.history import save_post_snapshot

    today = datetime.now(UTC).date()
    since = today - timedelta(days=90)
    snapshot_week = today - timedelta(days=today.weekday())  # Monday of this week

    try:
        posts = await meta_insights.fetch_recent_posts(since, today, max_posts=200)
        await save_post_snapshot(posts, snapshot_week)
    except Exception as exc:
        logger.error("weekly post snapshot failed: {!r}", exc)


def _daily() -> None:
    asyncio.create_task(_run_daily_snapshot())


def _weekly_snapshot() -> None:
    asyncio.create_task(_run_weekly_post_snapshot())


def start_scheduler() -> None:
    _scheduler.add_job(_weekly, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="UTC"),
                       id="weekly_report", replace_existing=True)
    _scheduler.add_job(_monthly, CronTrigger(day=1, hour=9, minute=0, timezone="UTC"),
                       id="monthly_report", replace_existing=True)
    _scheduler.add_job(_daily, CronTrigger(hour=8, minute=0, timezone="UTC"),
                       id="daily_snapshot", replace_existing=True)
    _scheduler.add_job(_weekly_snapshot, CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="UTC"),
                       id="weekly_post_snapshot", replace_existing=True)
    _scheduler.start()
    logger.info(
        "scheduler started: reports (Mon 09:00, 1st 09:00), "
        "daily snapshot 08:00, weekly post snapshot Mon 08:00 (all UTC)"
    )


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("report scheduler stopped")
