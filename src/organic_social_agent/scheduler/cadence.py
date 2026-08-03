"""Cadence policy + gap detection — the "looking alive" baseline.

Briefing: consistency is the floor (line 51). The minimum to pass a credibility
check is 4x Instagram/week and every-other-day TikTok (line 54); the agent must
flag when cadence is slipping (line 52). This module encodes those targets and
answers "are we on track this week?" — it holds NO state itself, it reads the
queue.

No credentials required — pure policy over queue counts.
"""

from __future__ import annotations

from organic_social_agent.scheduler.models import Platform

# Weekly minimum floor per platform (briefing line 54).
WEEKLY_TARGET: dict[Platform, int] = {
    Platform.INSTAGRAM: 4,   # 4x per week
    Platform.TIKTOK: 4,      # every other day ≈ 3-4/week
}

# Target mix across a week's Instagram slots (briefing "Format Logic").
# Tunable; the planner uses it to shape the week.
IG_FORMAT_MIX = {"carousel": 0.4, "reel": 0.4, "story": 0.2}


def week_gap(platform: Platform, scheduled_count: int) -> int:
    """How many posts short of the weekly floor. >0 means cadence is slipping."""
    return max(0, WEEKLY_TARGET[platform] - scheduled_count)


def is_slipping(counts_by_platform: dict[Platform, int]) -> dict[Platform, int]:
    """Return {platform: shortfall} for any platform under its floor. TODO: wire
    to queue.count_scheduled_this_week and surface via Slack."""
    raise NotImplementedError
