"""Live trend search — current Instagram/TikTok trends via Claude web search.

Called on every strategy question so the agent isn't limited to its training
cutoff. Returns a plain-text context block listing trending formats, hooks, and
audio themes for fashion/jewelry brands right now.

If the web search fails or times out, returns an empty string — the caller
falls back to Claude's built-in knowledge gracefully.
"""

from __future__ import annotations

from loguru import logger

from organic_social_agent.settings import settings

_TREND_PROMPT = (
    "Search the web for the most current information and return the top 5-6 "
    "trending content formats, hooks, and audio/music trends on Instagram Reels "
    "and TikTok RIGHT NOW for fashion and jewelry brands. Focus on: video formats "
    "or structures going viral in the last 2-4 weeks; hook styles gaining traction; "
    "trending audio themes for lifestyle/jewelry content; early-mover formats from "
    "TikTok not yet saturated on Instagram. Be specific and actionable. "
    "Return plain bullet points only — no intro, no headers."
)


async def fetch_live_trends() -> str:
    """Fetch current platform trends via Claude with web search.

    Returns a plain-text string ready to append to the strategy context, or
    empty string if the call fails.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    messages: list[dict] = [{"role": "user", "content": _TREND_PROMPT}]
    tools = [{"type": "web_search_20250305", "name": "web_search"}]

    try:
        for _turn in range(6):
            response = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=600,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                logger.info("live trend fetch OK ({} chars)", len(text))
                return text

            if response.stop_reason != "tool_use":
                break

            # Continue the agentic loop — server-side tool, send empty results back
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in response.content
                if b.type == "tool_use"
            ]
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                break

    except Exception as exc:
        logger.warning("live trend fetch failed: {!r}", exc)

    return ""
