"""Generate structured single-post content briefs via Claude.

Called by the /social brief [topic] slash command. Always uses the full
90-day strategy context (Meta + history) — there's no "is this a strategy
question?" gate; every brief request needs the full picture.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from loguru import logger

from organic_social_agent.settings import settings

_BRIEF_SYSTEM_PROMPT = """You are the Social Agent for Hey Harper — a waterproof, tarnish-free, \
lifetime-colour-guarantee jewelry brand sold DTC and at Target in the US.

Hey Harper brand context:
• Core promise: waterproof, tarnish-free, lifetime colour guarantee — jewelry that survives real life \
(sweat, swimming, showers, sports)
• Sold DTC (heyharper.com) and at Target stores across the US
• Key products: engraved/personalised pieces, Sub Box subscription (monthly jewelry box), \
Maré collection, best-selling stacks, bridal jewellery
• Content verticals: Sub Box unboxing/reveal, Sweatproof/Proof Lab (durability demos), \
stack styling, sports & athletes, university life, best sellers, Maré/Summer
• Audience: primarily women 18–35, US-based, active lifestyle, values quality + longevity
• Team posts 4× per week on Instagram Reels and feed. Humans create and post all content — \
you advise, you do not publish.

Your task: produce a structured content brief for a single Instagram post or Reel.

Output format — use exactly these section headers in *bold*, Slack mrkdwn:

*HOOK*
One specific opening line or visual moment. Concrete, not generic — not "show the product" \
but "open on hands under running water, ring still on."

*ANGLE*
The story or human truth in one sentence. What is this post really selling?

*FORMAT*
Reel / Carousel / Static — and one sentence on why this format fits this specific idea.

*CAPTION DIRECTION*
3–5 bullets: tone, key phrases to hit, what NOT to say, approximate length.

*CTA*
One specific call to action that fits the vibe — not just "shop now."

*REFERENCE POSTS*
Up to 3 posts from Hey Harper's own data that are most relevant to this brief. \
Include the permalink for each. If none are directly relevant, say so.

Rules:
• Anchor every section to a Hey Harper brand vertical (Sub Box, Sweatproof/Proof Lab, \
  stack styling, sports & athletes, university life, best sellers, Maré/Summer, \
  personalised/engraved). The vertical is the foundation — the cultural moment or trend \
  is the amplifier that makes it timely.
• Always include a current cultural moment, seasonal hook, or trending format in the \
  HOOK or ANGLE section. If the topic naturally crosses a brand vertical with a cultural \
  moment, make that crossover the central idea.
• Ground reference posts in the data provided — only link posts that appear in the data.
• Be specific everywhere. Vague briefs are useless — the team should be able to brief a \
  creator directly from this output.
• Keep the full brief under 380 words.
• End with one line: "Based on: [data sources used]"
• Use plain Slack mrkdwn only: *bold*, _italic_, bullet points. No ##headers, no code blocks."""


async def generate_brief(topic: str, post_days: int = 90) -> str:
    """Fetch Instagram context and ask Claude to produce a structured post brief.

    post_days controls how far back posts are fetched (default 90, from --days flag).
    Account-level KPIs are always capped at 30 days (Meta API limit).
    """
    from organic_social_agent.reporting import meta_insights
    from organic_social_agent.reporting.report import detect_standouts
    from organic_social_agent.strategy.memory import fetch_history_context
    from organic_social_agent.strategy.listening import fetch_competitor_context
    import anthropic

    until = date.today()
    kpi_since = until - timedelta(days=30)
    post_since = until - timedelta(days=max(post_days, 1))

    handles = [h.strip() for h in settings.competitor_ig_handles.split(",") if h.strip()]
    meta_task = asyncio.gather(
        meta_insights.fetch_account_kpis(kpi_since, until),
        meta_insights.fetch_recent_posts(post_since, until, max_posts=120),
        return_exceptions=True,
    )
    (meta_results, history_ctx, competitor_ctx) = await asyncio.gather(
        meta_task, fetch_history_context(), fetch_competitor_context(handles),
        return_exceptions=True,
    )

    lines: list[str] = [
        f"## Hey Harper Instagram — KPIs last 30 days ({kpi_since} to {until}), posts last {post_days} days ({post_since} to {until})"
    ]

    meta_ok = (
        not isinstance(meta_results, Exception)
        and isinstance(meta_results, (list, tuple))
        and not any(isinstance(r, Exception) for r in meta_results)
    )

    if meta_ok:
        kpis, posts = meta_results
        standouts = detect_standouts(posts)
        top_posts = sorted(posts, key=lambda p: p.reach, reverse=True)[:15]

        if kpis.views == 0 and kpis.reach_total == 0:
            lines += [
                "(Account-level totals unavailable from Meta API — "
                "ground brief in per-post data and historical KPI snapshots below.)",
                f"Posts published in window: {len(posts)}",
            ]
        else:
            lines += [
                f"Total views: {kpis.views:,}",
                f"Non-follower reach: {kpis.non_follower_reach_pct:.1f}%",
                f"Saves + shares per 1k reach: {kpis.saves_shares_per_reach:.2f}",
                f"Profile visits → product clicks: {kpis.profile_to_click_pct:.1f}%",
                f"Posts published in window: {len(posts)}",
            ]
        lines += [
            "",
            "## Standout posts (beat period median by 1.5×+)",
        ]
        if standouts:
            for s in standouts:
                p = s.post
                lines.append(f'[{p.product_type or p.media_type}] "{p.short_caption(80)}"')
                for reason in s.reasons:
                    lines.append(f"  • {reason}")
                if p.permalink:
                    lines.append(f"  {p.permalink}")
        else:
            lines.append("(no standouts this period)")

        lines += ["", "## Recent posts by reach (for reference post selection)"]
        for p in top_posts:
            lines.append(
                f'• "{p.short_caption(80)}" — {p.reach:,} reach · '
                f"{p.saved + p.shares} saves+shares | {p.permalink or '(no link)'}"
            )
    else:
        logger.warning("Meta fetch failed for brief generation: {!r}", meta_results)
        lines.append("(Instagram data unavailable — brief from brand knowledge only)")

    if isinstance(history_ctx, str):
        lines += ["", history_ctx]
    if isinstance(competitor_ctx, str) and competitor_ctx:
        lines += ["", competitor_ctx]

    context = "\n".join(lines)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1200,
        system=_BRIEF_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Instagram data:\n{context}\n\nGenerate a content brief for: {topic}",
        }],
    )
    return response.content[0].text
