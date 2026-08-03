"""Assemble the KPI report and deliver it to Slack (or print it).

Pulls the current window's KPIs + posts, the prior equal-length window for
deltas, flags standout posts with a plain-English "why", records the window to
long-term history, and renders a scannable summary.

    uv run --system-certs report --weekly           # last 7 days, print only
    uv run --system-certs report --monthly --post   # last 30 days, send to Slack
    uv run --system-certs report --from 2026-06-01 --to 2026-07-01 --label June

Read-only against Meta; the only write is posting the summary into Slack (with
--post) and appending the KPI snapshot to history.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta

import truststore
from loguru import logger

from organic_social_agent.reporting import history, meta_insights
from organic_social_agent.reporting.schema import (
    Kpis,
    KpiSnapshot,
    PostPerformance,
    Report,
    Standout,
)
from organic_social_agent.settings import settings

truststore.inject_into_ssl()

# minimum reach (relative to the window median) for a post to qualify as a
# "resonance" standout — keeps a tiny-reach post with a freak save-rate out.
_RESONANCE_REACH_FLOOR = 0.5
_STANDOUT_MULT = 1.5   # a metric must beat the median by this to be called out
_MAX_STANDOUTS = 3


# ── standout detection ────────────────────────────────────────────────────────
def _median(values: list[float]) -> float:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def detect_standouts(posts: list[PostPerformance]) -> list[Standout]:
    """Rank posts and attach the reason(s) each beat the window's norm."""
    ranked = [p for p in posts if p.reach > 0]
    if len(ranked) < 3:
        # too few to have a meaningful "typical" — just surface the best by reach
        top = sorted(ranked, key=lambda p: p.reach, reverse=True)[:1]
        return [Standout(post=p, reasons=[f"Reached {p.reach:,} accounts"]) for p in top]

    med_reach = _median([p.reach for p in ranked])
    med_views = _median([p.views for p in ranked])
    med_reson = _median([p.saves_shares_per_reach for p in ranked])

    reasons: dict[str, list[str]] = {}

    def note(post: PostPerformance, reason: str) -> None:
        reasons.setdefault(post.media_id, []).append(reason)

    top_reach = max(ranked, key=lambda p: p.reach)
    if med_reach and top_reach.reach >= _STANDOUT_MULT * med_reach:
        note(top_reach, f"Reached {top_reach.reach:,} accounts — {top_reach.reach / med_reach:.1f}× your typical post")

    top_views = max(ranked, key=lambda p: p.views)
    if med_views and top_views.views >= _STANDOUT_MULT * med_views:
        note(top_views, f"{top_views.views:,} views — {top_views.views / med_views:.1f}× typical")

    eligible = [p for p in ranked if p.reach >= _RESONANCE_REACH_FLOOR * med_reach]
    if eligible:
        top_reson = max(eligible, key=lambda p: p.saves_shares_per_reach)
        if med_reson and top_reson.saves_shares_per_reach >= _STANDOUT_MULT * med_reson:
            note(
                top_reson,
                f"{top_reson.saved + top_reson.shares:,} saves + shares "
                f"({top_reson.saves_shares_per_reach / med_reson:.1f}× typical per reach) — "
                f"the strongest 'this resonated' signal",
            )

    by_id = {p.media_id: p for p in ranked}
    ordered = sorted(reasons.keys(), key=lambda mid: by_id[mid].reach, reverse=True)
    return [Standout(post=by_id[mid], reasons=reasons[mid]) for mid in ordered[:_MAX_STANDOUTS]]


# ── assembly ──────────────────────────────────────────────────────────────────
async def build_report(
    since: date, until: date, *, label: str = "", persist: bool = True
) -> Report:
    """Build a full Report for [since, until): current + previous KPIs + standouts."""
    label = label or _auto_label(since, until)
    logger.info("building {} report {} -> {}", label, since, until)

    kpis = await meta_insights.fetch_account_kpis(since, until)
    posts = await meta_insights.fetch_recent_posts(since, until, max_posts=100)

    # prior equal-length window for deltas
    span = until - since
    prev: Kpis | None = None
    try:
        prev = await meta_insights.fetch_account_kpis(since - span, since)
        if prev.reach_total == 0 and prev.views == 0:
            prev = await history.latest_before(since) or prev
    except Exception as exc:  # deltas are best-effort
        logger.warning("previous-window fetch failed: {!r}", exc)

    report = Report(
        label=label, platform="instagram", since=since, until=until,
        kpis=kpis, previous=prev, post_count=len(posts),
        standouts=detect_standouts(posts),
    )

    if persist:
        await history.save_snapshot(
            KpiSnapshot(captured_at=datetime.now(UTC).isoformat(), kpis=kpis)
        )
    return report


def _auto_label(since: date, until: date) -> str:
    days = (until - since).days
    if days <= 8:
        return "Weekly"
    if 27 <= days <= 31:
        return "Monthly"
    return "Custom"


# ── delta formatting ──────────────────────────────────────────────────────────
def _delta_pts(cur: float, prev: float | None) -> str:
    if prev is None:
        return ""
    d = cur - prev
    return f"  ({'+' if d >= 0 else '-'}{abs(d):.1f} pts vs prev)"


def _delta_pct(cur: float, prev: float | None) -> str:
    if not prev:
        return ""
    d = 100.0 * (cur - prev) / prev
    return f"  ({'+' if d >= 0 else '-'}{abs(d):.1f}% vs prev)"


# ── rendering ─────────────────────────────────────────────────────────────────
def render_text(r: Report) -> str:
    k, p = r.kpis, r.previous
    win = f"{r.since:%b %d} -> {r.until:%b %d, %Y}"
    lines = [
        f"*Instagram — {r.label} report*   {win}",
        "",
        "*The 4 KPIs*",
        f"1. Non-follower reach: {k.non_follower_reach_pct:.1f}%"
        f"{_delta_pts(k.non_follower_reach_pct, p.non_follower_reach_pct if p else None)}"
        f"   [{k.reach_non_follower:,} of {k.reach_total:,} reached]",
        f"2. Total views: {k.views:,}"
        f"{_delta_pct(k.views, p.views if p else None)}",
        f"3. Saves + shares / 1k reach: {k.saves_shares_per_reach:.2f}"
        f"{_delta_pct(k.saves_shares_per_reach, p.saves_shares_per_reach if p else None)}"
        f"   [{k.saves + k.shares:,} total]",
        f"4. Profile -> product clicks: {k.profile_to_click_pct:.1f}%"
        f"{_delta_pts(k.profile_to_click_pct, p.profile_to_click_pct if p else None)}"
        f"   [{k.website_clicks:,} of {k.profile_views:,} visits]",
        "",
        f"*Standouts* ({r.post_count} posts this period)",
    ]
    if not r.standouts:
        lines.append("- (no posts published in this window)")
    for s in r.standouts:
        post = s.post
        head = f"- [{post.product_type or post.media_type}] \"{post.short_caption(70)}\""
        lines.append(head)
        for reason in s.reasons:
            lines.append(f"    • {reason}")
        if post.permalink:
            lines.append(f"    {post.permalink}")
    return "\n".join(lines)


def render_slack_blocks(r: Report) -> list[dict]:
    """Block Kit blocks for chat.postMessage."""
    k, p = r.kpis, r.previous
    win = f"{r.since:%b %d} → {r.until:%b %d, %Y}"
    kpi_md = "\n".join([
        f"*1. Non-follower reach:* {k.non_follower_reach_pct:.1f}%"
        f"{_delta_pts(k.non_follower_reach_pct, p.non_follower_reach_pct if p else None)}",
        f"*2. Total views:* {k.views:,}{_delta_pct(k.views, p.views if p else None)}",
        f"*3. Saves + shares / 1k reach:* {k.saves_shares_per_reach:.2f}"
        f"{_delta_pct(k.saves_shares_per_reach, p.saves_shares_per_reach if p else None)}",
        f"*4. Profile → product clicks:* {k.profile_to_click_pct:.1f}%"
        f"{_delta_pts(k.profile_to_click_pct, p.profile_to_click_pct if p else None)}",
    ])
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Instagram — {r.label} report"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": win}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": kpi_md}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Standouts* — {r.post_count} posts this period"}},
    ]
    for s in r.standouts:
        post = s.post
        why = "\n".join(f"• {reason}" for reason in s.reasons)
        text = f"*[{post.product_type or post.media_type}]* {post.short_caption(90)}\n{why}"
        block: dict = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        if post.permalink:
            block["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "View post"},
                "url": post.permalink,
            }
        blocks.append(block)
    return blocks


def post_to_slack(r: Report, *, channel_id: str | None = None) -> bool:
    """Send the report to Slack. channel_id overrides SLACK_CHANNEL_ID if given."""
    target = channel_id or settings.slack_channel_id
    if not (settings.slack_bot_token and target):
        logger.warning("Slack not configured (SLACK_BOT_TOKEN / SLACK_CHANNEL_ID) — not posting.")
        return False
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    client = WebClient(token=settings.slack_bot_token)
    try:
        client.chat_postMessage(
            channel=target,
            text=f"Instagram {r.label} report {r.since} → {r.until}",
            blocks=render_slack_blocks(r),
        )
        logger.success("posted {} report to Slack channel {}", r.label, target)
        return True
    except SlackApiError as exc:
        logger.error("Slack post failed: {}", exc.response.get("error"))
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_window(args) -> tuple[date, date]:
    until = date.fromisoformat(args.to) if args.to else datetime.now(UTC).date()
    if args.weekly:
        return until - timedelta(days=7), until
    if args.monthly:
        return until - timedelta(days=30), until
    if args.from_:
        return date.fromisoformat(args.from_), until
    return until - timedelta(days=7), until  # default: weekly


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # captions carry emoji
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Build & deliver the Instagram KPI report.")
    parser.add_argument("--from", dest="from_", help="window start YYYY-MM-DD")
    parser.add_argument("--to", help="window end YYYY-MM-DD (default: today)")
    parser.add_argument("--weekly", action="store_true", help="last 7 days")
    parser.add_argument("--monthly", action="store_true", help="last 30 days")
    parser.add_argument("--label", default="", help="override the report label")
    parser.add_argument("--post", action="store_true", help="send to Slack (default: print only)")
    parser.add_argument("--no-persist", action="store_true", help="don't append to KPI history")
    args = parser.parse_args()

    since, until = _parse_window(args)
    report = asyncio.run(
        build_report(since, until, label=args.label, persist=not args.no_persist)
    )
    print("\n" + render_text(report) + "\n")
    if args.post:
        post_to_slack(report)


if __name__ == "__main__":
    main()
