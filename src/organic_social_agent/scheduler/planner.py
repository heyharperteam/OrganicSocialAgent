"""Weekly slot planner — generates the empty schedule the week should hit.

Separation of concerns:
  - planner (here)  decides WHEN + WHAT SHAPE: builds the week's slots from the
                    cadence policy (4x IG / every-other-day TikTok), assigning
                    each slot a platform, format, content_type (50/30/20), and a
                    timezone-aware datetime.
  - curation        decides WHICH ASSET fills each slot (from the library index).
  - approval        a human signs off → slots become queued Posts.
  - worker          fires them at scheduled_at.

So the planner emits *unfilled* slots; curation turns them into draft Posts.
No credentials required — pure scheduling logic over the cadence policy.
"""

from __future__ import annotations

from pydantic import BaseModel

from organic_social_agent.scheduler.models import (
    AccountTarget,
    ContentType,
    Platform,
    PostFormat,
)


class Slot(BaseModel):
    """An unfilled schedule slot the planner proposes for the week."""
    platform: Platform
    account: AccountTarget = AccountTarget.MAIN
    format: PostFormat
    content_type: ContentType
    scheduled_at: str   # ISO-8601, UTC


def plan_week(week_start_iso: str) -> list[Slot]:
    """Build the week's slots from cadence policy + format mix + 50/30/20 split.

    TODO:
      - 4 IG slots (≈40% carousel / 40% reel / 20% story) across the week
      - every-other-day TikTok slots
      - tag each slot campaign/ugc/series toward the 50/30/20 target
      - posting times in the brand's timezone (decision below)
      - optionally emit warmed-account slots for series pilots (Fastlane)
    """
    raise NotImplementedError
