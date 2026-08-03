"""Slack Events API handler — app_mention events from #social-media-strategy.

Flow:
  1. POST /slack/events arrives (Slack sends all subscribed events here)
  2. Signature is verified upstream in server.py
  3. URL verification challenge is handled immediately
  4. app_mention events are acknowledged with 200 instantly, then processed
     in the background via asyncio.create_task so Slack's 3-second window
     is never at risk

The background task:
  - Builds a Hey Harper data context from the last 30 days of Instagram metrics
  - Sends the user's question + context to Claude
  - Posts Claude's response back to the channel (threaded to the mention)
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta

import httpx
import truststore
from loguru import logger

from organic_social_agent.settings import settings

truststore.inject_into_ssl()

_SYSTEM_PROMPT = """You are the Social Agent for Hey Harper — a waterproof, tarnish-free, \
lifetime-colour-guarantee jewelry brand sold DTC and at Target in the US.

Your job: answer questions about Hey Harper's Instagram performance and content strategy \
inside the team's #social-media-strategy Slack channel. The team @-mentions you to ask \
for post ideas, performance explanations, or content calendar recommendations.

Hey Harper brand context:
• Core promise: waterproof, tarnish-free, lifetime colour guarantee — jewelry that \
  survives real life (sweat, swimming, showers, sports)
• Sold DTC (heyharper.com) and at Target stores across the US
• Key products: engraved/personalised pieces, Sub Box subscription (monthly jewelry box), \
  Maré collection, best-selling stacks, bridal jewellery
• Content verticals: Sub Box unboxing/reveal, Sweatproof/Proof Lab (durability demos), \
  stack styling, sports & athletes, university life, best sellers, Maré/Summer
• Audience: primarily women 18–35, US-based, active lifestyle, values quality + longevity
• The team posts 4× per week on Instagram Reels and feed. Humans create and post all \
  content — you advise, you do not publish.

Rules for all responses:
• Ground performance analysis in the metrics provided. Never invent numbers.
• Be specific: name posts, formats, products, and hooks. Vague advice is useless.
• Be concise — this is Slack. Lead with the direct answer, then the reasoning. \
  Keep responses under 400 words unless a calendar is requested.
• Use plain Slack markdown: *bold*, _italic_, bullet points with •. No headers (##). \
  No code blocks.
• At the end of every response add one line: "Based on: [sources used]" — \
  list only what you actually drew from.

Rules for content suggestions and strategy:
• Brand verticals come first. Every suggestion must be rooted in one of Hey Harper's \
  content verticals: Sub Box unboxing/reveal, Sweatproof/Proof Lab (durability demos), \
  stack styling, sports & athletes, university life, best sellers, Maré/Summer, \
  personalised/engraved pieces. Never lead with a trend or cultural moment that isn't \
  anchored to a vertical — Hey Harper's identity is the constant, trends are the amplifier.
• Always add a cultural moment. Every suggestion must include a current cultural moment, \
  seasonal hook, or trending format that makes the vertical land harder right now. \
  A great suggestion is "vertical + moment" — not one or the other.
• Prioritise crossovers. When a cultural moment maps directly onto a Hey Harper vertical \
  (e.g. back-to-school season × university life, summer heat wave × Sweatproof), that \
  intersection is the highest-confidence idea. Lead with these.
• Use trend knowledge to sharpen the format. Draw on your knowledge of viral content \
  formats, trending audio themes, and popular hooks on TikTok and Instagram Reels. Be \
  specific — name the format or structure, explain why it fits this vertical, and flag \
  early-mover opportunities (gaining on TikTok but not yet on Instagram).
• Use own data to validate. Prioritise verticals and formats proven in Hey Harper's \
  performance data. If the data shows a vertical consistently outperforms, lead with it.
• Use competitor data to find gaps. When competitor data is provided, spot formats or \
  topics getting strong engagement that Hey Harper hasn't tried — and flag if a \
  competitor is winning on a vertical Hey Harper owns (waterproof, personalised pieces).
• Use historical KPI data to identify long-term patterns across months, not just the \
  current period."""


# Keywords that indicate the user wants suggestions or strategy, not just data
_STRATEGY_KEYWORDS = frozenset([
    "post", "posting", "suggest", "recommend", "strategy", "next week", "next month",
    "ideas", "content", "plan", "should we", "what to", "brief",
    "calendar", "trend", "upcoming", "create", "what should", "going to post",
    "schedule", "hook", "series", "vertical", "competitor", "competitors",
])


def _is_strategy_question(text: str) -> bool:
    """Return True if the question is asking for suggestions or strategy."""
    lowered = text.lower()
    return any(kw in lowered for kw in _STRATEGY_KEYWORDS)


_ALL_DATA_RE = re.compile(
    r"\b(?:using\s+)?all\s+available\s+data"
    r"|\ball\s+(?:data|time|history)\b"
    r"|\bfull\s+history\b"
    r"|\bmaximum\s+time(?:\s*frame)?\b"
    r"|\bsince\s+the\s+beginning\b",
    re.IGNORECASE,
)


def _is_all_data_request(text: str) -> bool:
    return bool(_ALL_DATA_RE.search(text))


_WINDOW_RE = re.compile(
    r"(?:last|past)\s+(\d+)\s+(day|week|month)s?"
    r"|last\s+(week|month)"
    r"|this\s+(week|month)"
    r"|(\d+)\s+(day|week|month)s?\s+ago",
    re.IGNORECASE,
)
_MULTIPLIERS = {"day": 1, "week": 7, "month": 30}


def _parse_window(text: str) -> int | None:
    """Extract an explicit day count from natural language, or return None for defaults.

    Examples: 'last 2 weeks' → 14, 'past 60 days' → 60, 'last month' → 30.
    """
    m = _WINDOW_RE.search(text)
    if not m:
        return None
    n_str, unit, bare_unit, this_unit, ago_n, ago_unit = m.groups()
    if n_str and unit:
        return int(n_str) * _MULTIPLIERS[unit.lower()]
    if bare_unit or this_unit:
        return _MULTIPLIERS[(bare_unit or this_unit).lower()]
    if ago_n and ago_unit:
        return int(ago_n) * _MULTIPLIERS[ago_unit.lower()]
    return None


async def _build_context(question: str, post_days: int = 90) -> str:
    """Pull Instagram data as a plain-text context block for Claude.

    post_days controls how far back posts are fetched (default 90). The
    account-level KPI window is always capped at 30 days (Meta API limit).
    For strategy/suggestion questions, also fetches historical KPI patterns
    and competitor data concurrently.
    """
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.report import detect_standouts

    until = date.today()
    # Meta account-level insights API rejects windows > 30 days; per-post endpoint has no limit
    kpi_since = until - timedelta(days=30)
    post_since = until - timedelta(days=max(post_days, 1))

    is_strategy = _is_strategy_question(question)

    # Fan out: always fetch Meta data; add history for strategy questions
    meta_task = asyncio.gather(
        meta_insights.fetch_account_kpis(kpi_since, until),
        meta_insights.fetch_recent_posts(post_since, until, max_posts=120),
        return_exceptions=True,
    )

    if is_strategy:
        from organic_social_agent.strategy.memory import fetch_history_context
        from organic_social_agent.strategy.listening import fetch_competitor_context
        from organic_social_agent.strategy.trends import fetch_live_trends
        from organic_social_agent.reporting.meta_insights import (
            fetch_recent_stories, fetch_audience_demographics,
        )
        handles = [h.strip() for h in settings.competitor_ig_handles.split(",") if h.strip()]
        (meta_results, history_ctx, competitor_ctx,
         stories, demographics, trend_ctx) = await asyncio.gather(
            meta_task,
            fetch_history_context(),
            fetch_competitor_context(handles),
            fetch_recent_stories(),
            fetch_audience_demographics(),
            fetch_live_trends(),
            return_exceptions=True,
        )
    else:
        from organic_social_agent.reporting.meta_insights import fetch_recent_stories
        meta_results, stories = await asyncio.gather(
            meta_task, fetch_recent_stories(), return_exceptions=True,
        )
        history_ctx = None
        competitor_ctx = None
        demographics = None
        trend_ctx = None

    # Unpack Meta results (may be exceptions if the API failed)
    if isinstance(meta_results, Exception) or (
        isinstance(meta_results, (list, tuple))
        and any(isinstance(r, Exception) for r in meta_results)
    ):
        logger.warning("Meta context fetch failed: {!r}", meta_results)
        return "(Instagram data unavailable — answering from brand knowledge only.)"

    kpis, posts = meta_results
    standouts = detect_standouts(posts)
    top_posts = sorted(posts, key=lambda p: p.reach, reverse=True)[:10]

    kpi_header: list[str]
    if kpis.views == 0 and kpis.reach_total == 0:
        kpi_header = [
            f"## Hey Harper Instagram — KPIs last 30 days ({kpi_since} to {until}), posts last {post_days} days ({post_since} to {until})",
            "(Account-level totals unavailable from Meta API — "
            "ground all analysis in per-post data and historical KPI snapshots below.)",
            f"Posts published in window: {len(posts)}",
        ]
    else:
        kpi_header = [
            f"## Hey Harper Instagram — KPIs last 30 days ({kpi_since} to {until}), posts last {post_days} days ({post_since} to {until})",
            f"Total views: {kpis.views:,}",
            f"Non-follower reach: {kpis.non_follower_reach_pct:.1f}% "
            f"({kpis.reach_non_follower:,} of {kpis.reach_total:,} reached)",
            f"Saves + shares per 1k reach: {kpis.saves_shares_per_reach:.2f}",
            f"Profile visits → product clicks: {kpis.profile_to_click_pct:.1f}%",
            f"Posts published in window: {len(posts)}",
        ]

    lines = kpi_header + [
        "",
        "## Standout posts (beat the period median by 1.5×+)",
    ]
    if standouts:
        for s in standouts:
            p = s.post
            lines.append(f'[{p.product_type or p.media_type}] "{p.short_caption(100)}"')
            for reason in s.reasons:
                lines.append(f"  • {reason}")
            if p.permalink:
                lines.append(f"  {p.permalink}")
    else:
        lines.append("(no standouts this period)")

    lines += ["", "## Top 10 posts by reach"]
    for p in top_posts:
        follower_note = (
            f" ({p.non_follower_reach_pct:.0f}% non-followers)"
            if p.reach_follower or p.reach_non_follower else ""
        )
        reel_note = (
            f" · avg watch {p.avg_watch_time_ms / 1000:.1f}s"
            f" · {p.completion_rate * 100:.0f}% completion"
            if p.product_type == "REELS" and p.avg_watch_time_ms else ""
        )
        lines.append(
            f'• [{p.product_type or p.media_type}] "{p.short_caption(80)}"'
            f" — {p.reach:,} reach{follower_note} · {p.views:,} views"
            f" · {p.saved + p.shares} saves+shares{reel_note} | {p.permalink}"
        )

    # Active stories (always included when available)
    if isinstance(stories, list) and stories:
        lines += ["", "## Active stories (last 24 h)"]
        for s in stories:
            lines.append(
                f'• "{s.short_caption(80)}"'
                f" — {s.impressions:,} impressions · {s.reach:,} reach"
                f" · {s.taps_back} tap-backs · {s.exits} exits"
                + (f" · {s.story_replies} replies" if s.story_replies else "")
            )

    # Strategy-only sources
    if is_strategy and isinstance(history_ctx, str):
        lines += ["", history_ctx]
    if is_strategy and isinstance(competitor_ctx, str) and competitor_ctx:
        lines += ["", competitor_ctx]
    if is_strategy and isinstance(demographics, object) and not isinstance(demographics, Exception):
        from organic_social_agent.reporting.schema import AudienceDemographics
        if isinstance(demographics, AudienceDemographics) and demographics.gender_age:
            top_ga = sorted(demographics.gender_age.items(), key=lambda x: -x[1])[:6]
            total_ga = sum(demographics.gender_age.values()) or 1
            ga_str = ", ".join(
                f"{k} ({v / total_ga * 100:.0f}%)" for k, v in top_ga
            )
            top_countries = sorted(demographics.top_countries.items(), key=lambda x: -x[1])[:5]
            total_c = sum(demographics.top_countries.values()) or 1
            c_str = ", ".join(f"{k} ({v / total_c * 100:.0f}%)" for k, v in top_countries)
            top_cities = sorted(demographics.top_cities.items(), key=lambda x: -x[1])[:5]
            lines += [
                "",
                "## Audience demographics (current snapshot)",
                f"Age/gender: {ga_str}",
                f"Top countries: {c_str}",
                f"Top cities: {', '.join(c for c, _ in top_cities)}",
            ]
    if is_strategy and isinstance(trend_ctx, str) and trend_ctx:
        lines += ["", "## Current Instagram/TikTok trends (live)", trend_ctx]

    return "\n".join(lines)


async def _fetch_thread_turns(
    channel: str, thread_ts: str, current_ts: str
) -> list[dict]:
    """Return up to 3 prior Q&A pairs from the Slack thread, oldest first.

    Each dict has keys 'user' (the question text) and 'assistant' (the bot reply).
    The current message (current_ts) is excluded.
    """
    if not settings.slack_bot_token:
        return []

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            "https://slack.com/api/conversations.replies",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            params={"channel": channel, "ts": thread_ts, "limit": 30},
            timeout=10,
        )
    data = resp.json()
    if not data.get("ok"):
        logger.warning("conversations.replies failed: {}", data.get("error"))
        return []

    turns: list[dict] = []
    pending_question: str | None = None

    for msg in data.get("messages", []):
        if msg.get("ts") == current_ts:
            break  # stop before the current message

        is_bot = bool(msg.get("bot_id") or msg.get("subtype") == "bot_message")
        text = msg.get("text", "").strip()

        if not is_bot:
            # Only pick up messages that @-mention the bot; ignore plain thread chat
            if re.search(r"<@[A-Z0-9]+>", text):
                clean = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
                if clean:
                    pending_question = clean
        elif pending_question:
            # Bot reply — skip the "On it…" acknowledgment, keep the real answer
            if not text.startswith("_On it_"):
                turns.append({"user": pending_question, "assistant": text})
                pending_question = None

    return turns[-3:]  # at most 3 prior turns


async def _call_claude(
    question: str,
    context: str,
    prior_turns: list[dict] | None = None,
) -> str:
    """Send the question + Hey Harper data context to Claude and return the reply.

    If prior_turns is provided (follow-up in a thread), they are prepended as
    conversation history so Claude has full context of the exchange so far.
    Prior turns carry only the Q&A text; the fresh data context goes in the
    final (current) user message only.
    """
    import anthropic

    messages: list[dict] = []

    for turn in (prior_turns or []):
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["assistant"]})

    messages.append({
        "role": "user",
        "content": f"Instagram data context:\n{context}\n\nQuestion from the team: {question}",
    })

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        messages=messages,
    )
    return response.content[0].text


async def _post_message(channel: str, text: str, *, thread_ts: str | None = None) -> None:
    """Post a plain-text message to a Slack channel via chat.postMessage."""
    if not settings.slack_bot_token:
        logger.warning("SLACK_BOT_TOKEN not set — cannot post mention reply")
        return

    payload: dict = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json=payload,
            timeout=10,
        )
    data = resp.json()
    if not data.get("ok"):
        logger.error("chat.postMessage failed: {}", data.get("error"))


async def _handle_mention(event: dict) -> None:
    """Process an app_mention in the background — build context, call Claude, reply."""
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    user = event.get("user", "")

    # Strip the bot mention token(s) from the message text
    raw_text = event.get("text", "")
    question = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()

    if not question:
        await _post_message(channel,
            f"Hey <@{user}> — ask me anything about Hey Harper's performance or "
            "what to post next. Try: _@Social Agent what should we post next week?_",
            thread_ts=thread_ts)
        return

    # Acknowledge immediately so the user knows something is happening
    await _post_message(channel, f"_On it_ <@{user}>…", thread_ts=thread_ts)

    try:
        # If this is a reply inside an existing thread, fetch prior Q&A turns
        # so Claude has conversation context (e.g. "make it shorter", follow-ups)
        is_thread_reply = bool(event.get("thread_ts") and event.get("thread_ts") != event.get("ts"))
        prior_turns: list[dict] = []
        if is_thread_reply:
            prior_turns = await _fetch_thread_turns(channel, thread_ts, event.get("ts", ""))

        from organic_social_agent.reporting.history import oldest_since

        is_all = _is_all_data_request(question)
        post_days = None if is_all else _parse_window(question)

        if not is_all and post_days is None:
            await _post_message(
                channel,
                (
                    "What time period should I look at? "
                    "Add a window to your question — e.g. "
                    "_last 7 days_, _last 30 days_, _last 2 weeks_, or _last 3 months_. "
                    "Or say _using all available data_ to use the full history."
                ),
                thread_ts=thread_ts,
            )
            return

        oldest = await oldest_since()
        db_days = (date.today() - oldest).days if oldest else 0
        effective_cap = max(90, db_days)

        if is_all:
            post_days = effective_cap
        elif post_days > effective_cap:
            await _post_message(
                channel,
                (
                    f"The furthest back we can go is *{effective_cap} days* "
                    f"({'Meta API limit' if effective_cap == 90 else 'based on stored history'}). "
                    f"Please specify a shorter window (e.g. _last {effective_cap} days_) "
                    f"or say _using all available data_ to use the full {effective_cap} days."
                ),
                thread_ts=thread_ts,
            )
            return

        context = await _build_context(question, post_days=post_days)
        answer = await _call_claude(question, context, prior_turns=prior_turns)
        await _post_message(channel, answer, thread_ts=thread_ts)
    except Exception as exc:
        logger.exception("mention handler error")
        await _post_message(
            channel,
            "Sorry, I hit an error processing that. Try again in a moment.",
            thread_ts=thread_ts,
        )


async def handle_event(body: bytes) -> dict:
    """Entry point called by server.py after signature verification."""
    payload = json.loads(body)

    # Slack sends this once when you first save the Events URL in the app config
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    event = payload.get("event", {})
    if event.get("type") == "app_mention":
        asyncio.create_task(_handle_mention(event))

    return {"ok": True}
