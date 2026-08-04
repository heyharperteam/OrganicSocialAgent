"""Slack approval UI (Block Kit) — the semi-autonomous gate.

Posts each proposed draft as a card (asset preview, caption, target time) with
Approve / Edit / Reject buttons into settings.slack_channel_id. Button clicks
arrive at /slack/interactivity and flip the post's status via scheduler.queue:
    Approve → APPROVED (now eligible for the worker)
    Reject  → REJECTED
    Edit    → open a modal to tweak caption/time, then re-post the card

Reuses the PPB Slack-job pattern (bot token + signing-secret verification).
Nothing reaches the worker without an Approve here — the brand-safety rule.
"""

from __future__ import annotations

from loguru import logger

from organic_social_agent.scheduler.models import Post


async def post_proposal(posts: list[Post]) -> None:
    """Render each draft as an approval card in Slack. TODO."""
    logger.info("posting {} proposal card(s) to Slack", len(posts))
    raise NotImplementedError


async def handle_interaction(payload: dict) -> None:
    """Verify + apply an Approve/Edit/Reject action. TODO."""
    raise NotImplementedError
