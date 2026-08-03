"""Slash command handlers — /report and /social.

Slack slash commands send application/x-www-form-urlencoded to the registered
request URL and expect a response within 3 seconds. Anything that takes longer
(like building a report) responds immediately with an ephemeral acknowledgement
then posts the real result via chat.postMessage in a background task.

Commands:
  /report [last-7-days | last-30-days | from YYYY-MM-DD to YYYY-MM-DD]
      Builds an Instagram KPI report and posts it to the channel.

  /social config
      (stub) Will expose runtime config table knobs once Postgres is wired.

  /social brief [topic]
      Generates a structured content brief (hook, angle, format, caption,
      CTA, reference posts) grounded in 90 days of Instagram data.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

import httpx
import truststore
from loguru import logger

from organic_social_agent.settings import settings

truststore.inject_into_ssl()

_DATE_RE = re.compile(
    r"from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)


def _parse_report_window(text: str) -> tuple[date, date]:
    """Parse /report argument into (since, until). Defaults to last 7 days."""
    text = text.strip().lower()
    until = date.today()
    if "30" in text or "monthly" in text or "month" in text:
        return until - timedelta(days=30), until
    m = _DATE_RE.search(text)
    if m:
        return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    return until - timedelta(days=7), until  # default: weekly


async def _post_message(channel: str, text: str, blocks: list | None = None) -> None:
    if not settings.slack_bot_token:
        return
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json=payload,
            timeout=15,
        )
    data = resp.json()
    if not data.get("ok"):
        logger.error("chat.postMessage failed: {}", data.get("error"))


async def _run_report_command(channel_id: str, text: str) -> None:
    """Build the report and post it — runs in the background."""
    from organic_social_agent.reporting.report import build_report, render_slack_blocks

    since, until = _parse_report_window(text)
    label = "Monthly" if (until - since).days >= 27 else "Weekly"

    try:
        report = await build_report(since, until, label=label, persist=True)
        blocks = render_slack_blocks(report)
        fallback = f"Instagram {label} report {since} → {until}"
        await _post_message(channel_id, fallback, blocks=blocks)
    except Exception as exc:
        logger.error("/report command failed: {!r}", exc)
        await _post_message(channel_id, f"Sorry, the report failed: {exc!r}")


async def handle_report(form: dict) -> dict:
    """Handle /report — ack immediately, build in background."""
    text = form.get("text", "")
    channel_id = form.get("channel_id", "")
    user_id = form.get("user_id", "")

    since, until = _parse_report_window(text)
    label = "monthly" if (until - since).days >= 27 else "weekly"

    asyncio.create_task(_run_report_command(channel_id, text))

    return {
        "response_type": "ephemeral",
        "text": f"Pulling your {label} report ({since} → {until})… :bar_chart:",
    }


_DAYS_RE = re.compile(r"--days\s+(\d+|all)", re.IGNORECASE)
_ALL_DATA_RE = re.compile(
    r"\b(?:using\s+)?all\s+available\s+data"
    r"|\ball\s+(?:data|time|history)\b"
    r"|\bfull\s+history\b"
    r"|\bmaximum\s+time(?:\s*frame)?\b",
    re.IGNORECASE,
)


async def _run_brief_command(channel_id: str, user_id: str, topic: str, post_days: int) -> None:
    """Generate the brief and post it to the channel — runs in the background."""
    from organic_social_agent.strategy.briefs import generate_brief

    try:
        brief = await generate_brief(topic, post_days=post_days)
        header = f"📋 *Content Brief — {topic}*\n_Requested by <@{user_id}>_\n\n"
        await _post_message(channel_id, header + brief)
    except Exception as exc:
        logger.error("/social brief failed: {!r}", exc)
        await _post_message(
            channel_id,
            f"Sorry <@{user_id}>, the brief generation failed. Try again in a moment.",
        )


async def handle_social(form: dict) -> dict:
    """Handle /social subcommands."""
    raw_text = form.get("text", "").strip()
    parts = raw_text.split(None, 1)  # split into [subcommand, rest]
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "config":
        return {
            "response_type": "ephemeral",
            "text": (
                "*Social Agent config*\n"
                "Runtime config (report cadence, competitor handles, lookback windows) "
                "will be editable here once the Postgres config table is wired. Coming soon."
            ),
        }

    if sub == "brief":
        if not rest:
            return {
                "response_type": "ephemeral",
                "text": (
                    "*Usage:* `/social brief --days N [topic]`\n"
                    "• `/social brief --days 30 Sub Box reveal for back-to-school season`\n"
                    "• `/social brief --days 14 waterproof reel`\n"
                    "• `/social brief --days all waterproof reel` — uses full available history\n\n"
                    "`--days N` is required — it controls how far back post data is pulled."
                ),
            }
        channel_id = form.get("channel_id", "")
        user_id = form.get("user_id", "")
        days_match = _DAYS_RE.search(rest)
        is_all = bool(days_match and days_match.group(1).lower() == "all") or bool(
            _ALL_DATA_RE.search(rest)
        )

        if not days_match and not is_all:
            return {
                "response_type": "ephemeral",
                "text": (
                    "Please include `--days N` to set the lookback window.\n"
                    "• `/social brief --days 30 waterproof reel`\n"
                    "• `/social brief --days 14 sub box reveal`\n"
                    "• `/social brief --days all waterproof reel` — full history"
                ),
            }

        from organic_social_agent.reporting.history import oldest_since
        from datetime import date as _date

        oldest = await oldest_since()
        max_days = (_date.today() - oldest).days if oldest else None

        if is_all:
            post_days = max_days if max_days else 90
            topic = _DAYS_RE.sub("", _ALL_DATA_RE.sub("", rest)).strip()
            days_label = f"all available data ({post_days} days)"
        else:
            post_days = int(days_match.group(1))
            topic = _DAYS_RE.sub("", rest).strip()
            if max_days is not None and post_days > max_days:
                return {
                    "response_type": "ephemeral",
                    "text": (
                        f"We only have *{max_days} days* of data on record. "
                        f"Please use `--days {max_days}` or less, "
                        f"or `--days all` to use the full history."
                    ),
                }
            days_label = f"last {post_days} days"

        asyncio.create_task(_run_brief_command(channel_id, user_id, topic, post_days))
        return {
            "response_type": "ephemeral",
            "text": f"Generating brief for *{topic}* ({days_label})… should be ready in ~20 seconds. :pencil:",
        }

    if sub == "calendar":
        return {
            "response_type": "ephemeral",
            "text": (
                "*/social calendar* is coming soon. "
                "In the meantime, @-mention me in #social-media-strategy: "
                "_@Social Agent build me a content plan for August_"
            ),
        }

    return {
        "response_type": "ephemeral",
        "text": (
            "*Social Agent commands*\n"
            "• `/report [last-7-days | last-30-days | from YYYY-MM-DD to YYYY-MM-DD]` — KPI report\n"
            "• `/social brief [topic]` — structured content brief\n"
            "• `/social config` — view/edit runtime config _(coming soon)_\n\n"
            "Or @-mention me in #social-media-strategy with any question."
        ),
    }
