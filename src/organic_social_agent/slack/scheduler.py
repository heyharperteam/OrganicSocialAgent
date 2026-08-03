"""APScheduler jobs — automated weekly and monthly report drops.

Runs inside the FastAPI process on its asyncio loop. Both jobs build the report
and post it to #social-media-reporting.

Schedule (runtime-config defaults, will move to Postgres config table):
  Weekly  — every Monday at 09:00 local time
  Monthly — 1st of each month at 09:00 local time
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

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


def start_scheduler() -> None:
    _scheduler.add_job(_weekly, CronTrigger(day_of_week="mon", hour=9, minute=0),
                       id="weekly_report", replace_existing=True)
    _scheduler.add_job(_monthly, CronTrigger(day=1, hour=9, minute=0),
                       id="monthly_report", replace_existing=True)
    _scheduler.start()
    logger.info("report scheduler started (weekly Mon 09:00, monthly 1st 09:00)")


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("report scheduler stopped")
