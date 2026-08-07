"""APScheduler jobs — automated weekly and monthly report drops.

Runs inside the FastAPI process on its asyncio loop. Both jobs build the report
and post it to #social-media-reporting.

Schedule (runtime-config defaults, will move to Postgres config table):
  Weekly  — every Monday at 09:00 local time
  Monthly — 1st of each month at 09:00 local time
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import truststore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

truststore.inject_into_ssl()

_scheduler = AsyncIOScheduler()


async def _run_report(label: str, since: date, until: date, channel_id: str | None = None) -> None:
    from organic_social_agent.reporting.report import build_report, post_to_slack

    try:
        logger.info("scheduled {} report: {} → {}", label, since, until)
        report = await build_report(since, until, label=label, persist=True)
        posted = post_to_slack(report, channel_id=channel_id)
        if not posted:
            logger.warning("scheduled {} report built but Slack delivery skipped (no token?)", label)
    except Exception as exc:
        logger.error("scheduled {} report failed: {!r}", label, exc)


async def _weekly() -> None:
    until = date.today()
    since = until - timedelta(days=7)
    await _run_report("Weekly", since, until)


async def _monthly() -> None:
    until = date.today()
    since = until - timedelta(days=30)
    await _run_report("Monthly", since, until)


async def _run_daily_snapshot() -> None:
    """Save daily KPIs (yesterday's 1-day window) and today's active stories."""
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.history import save_daily_kpi, save_story_snapshot

    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    try:
        # The window is yesterday 00:00 → today 00:00, so the row belongs to
        # yesterday. Labelling it `today` (pre-v0.21) shifted every datapoint
        # one day forward and left the current day permanently missing.
        kpis = await meta_insights.fetch_account_kpis(yesterday, today)
        await save_daily_kpi(kpis, yesterday)
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


_TYPE_LABEL = {"IMAGE": "Photo", "VIDEO": "Video", "CAROUSEL_ALBUM": "Carousel"}


def filter_last_24h(posts: list, cutoff: datetime) -> list:
    """Return only posts whose timestamp is on or after cutoff."""
    recent = []
    for p in posts:
        if p.timestamp:
            try:
                ts = datetime.fromisoformat(p.timestamp.replace("Z", "+00:00"))
                if ts >= cutoff:
                    recent.append(p)
            except ValueError:
                pass
    return recent


def build_mentions_blocks(recent: list, today) -> list[dict]:
    """Build Block Kit blocks for a tags/mentions alert (shared by scheduler and /social mentions)."""
    date_str = f"{today.strftime('%B')} {today.day}, {today.year}"
    n = len(recent)

    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"\U0001f3f7️ Daily Tags — {date_str}", "emoji": True}},
    ]
    if not recent:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No new tags in the last 24 hours."},
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{n} post{'s' if n != 1 else ''} tagged @heyharper* in the last 24 hours:"},
        })
        blocks.append({"type": "divider"})
        for p in recent:
            type_label = _TYPE_LABEL.get(p.media_type.upper(), p.media_type or "Post")
            lines = [f"*@{p.username}* · {type_label} · ❤️ {p.like_count:,}"]
            cap = p.short_caption(120)
            if cap:
                lines.append(f"_{cap}_")
            if p.permalink:
                lines.append(p.permalink)
            blocks.append({"type": "section",
                            "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    return blocks


async def _run_daily_mentions(channel_id: str | None = None) -> None:
    """Fetch posts where the account was tagged and post a morning alert to Slack."""
    from organic_social_agent.reporting.meta_insights import fetch_tagged_posts
    from organic_social_agent.settings import settings as s

    today = datetime.now(UTC).date()
    cutoff = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=1)

    try:
        all_posts = await fetch_tagged_posts()
    except Exception as exc:
        logger.error("daily mentions fetch failed: {!r}", exc)
        return

    recent = filter_last_24h(all_posts, cutoff)

    target = channel_id or s.slack_channel_id
    if not (s.slack_bot_token and target):
        logger.warning("Slack not configured — skipping daily mentions post")
        return

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    blocks = build_mentions_blocks(recent, today)
    client = WebClient(token=s.slack_bot_token)
    try:
        client.chat_postMessage(
            channel=target,
            text=f"Daily tags — {len(recent)} post(s) tagged @heyharper in the last 24h",
            blocks=blocks,
        )
        logger.info("daily mentions alert posted: {} tagged post(s)", len(recent))
    except SlackApiError as exc:
        logger.error("daily mentions Slack post failed: {}", exc.response.get("error"))


def start_scheduler() -> None:
    # Every job below must be a coroutine function. AsyncIOScheduler awaits those
    # on the event loop, but hands plain functions to a worker thread — where
    # asyncio.create_task() raises "no running event loop" and the job silently
    # never runs. That bug ate every scheduled run before v0.20.
    _scheduler.add_job(_weekly, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="UTC"),
                       id="weekly_report", replace_existing=True)
    _scheduler.add_job(_monthly, CronTrigger(day=1, hour=9, minute=0, timezone="UTC"),
                       id="monthly_report", replace_existing=True)
    _scheduler.add_job(_run_daily_snapshot, CronTrigger(hour=8, minute=0, timezone="UTC"),
                       id="daily_snapshot", replace_existing=True)
    _scheduler.add_job(_run_weekly_post_snapshot,
                       CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="UTC"),
                       id="weekly_post_snapshot", replace_existing=True)
    _scheduler.add_job(_run_daily_mentions, CronTrigger(hour=9, minute=0, timezone="UTC"),
                       id="daily_mentions", replace_existing=True)
    _scheduler.start()
    logger.info(
        "scheduler started: reports (Mon 09:00, 1st 09:00), "
        "daily snapshot 08:00, weekly post snapshot Mon 08:00, "
        "daily mentions alert 09:00 (all UTC)"
    )


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("report scheduler stopped")
