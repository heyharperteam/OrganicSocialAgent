"""Domain model + state machine for the scheduler.

Shaped by the briefing's Priority-2 requirements:
  - semi-autonomous: only a human Approve advances draft → approved (line 48)
  - weekly schedule proposed, nothing queues before approval (line 45)
  - per-platform cadence + format (lines 93-101)
  - 50/30/20 content split (campaign/ugc/series)
  - warmed / Fastlane accounts as separate publish targets (lines 114-117)

State flow (only a human approval advances draft → approved):
    draft → approved → queued → published
                    ↘ rejected
    queued → failed (retryable) → queued ...
"""

from __future__ import annotations

import enum

from pydantic import BaseModel


class Platform(str, enum.Enum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class PostFormat(str, enum.Enum):
    # Instagram — each maps to a strategic goal (briefing "Format Logic")
    CAROUSEL = "carousel"   # engagement
    REEL = "reel"           # education
    STORY = "story"         # website sales / CTA
    # TikTok
    TIKTOK_VIDEO = "tiktok_video"


class ContentType(str, enum.Enum):
    """The 50/30/20 split the weekly plan must respect."""
    CAMPAIGN = "campaign"   # 50% — polished editorial / ecomm / drops
    UGC = "ugc"             # 30% — customer tags, Billo, community seeding
    SERIES = "series"       # 20% — Sub Box arcs, New Drop Highlights, BTS


class AccountTarget(str, enum.Enum):
    """Main profile vs. Fastlane warmed accounts (series incubator)."""
    MAIN = "main"
    WARMED_SWEAT = "warmed_sweat"        # persona 1: sweat proof / active girl
    WARMED_EVERYDAY = "warmed_everyday"  # persona 2: "I never take this off"
    WARMED_STACK = "warmed_stack"        # persona 3: stack styling / aesthetic


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    QUEUED = "queued"
    PUBLISHED = "published"
    FAILED = "failed"
    REJECTED = "rejected"


class Post(BaseModel):
    id: str
    platform: Platform
    account: AccountTarget = AccountTarget.MAIN
    format: PostFormat
    content_type: ContentType
    pillar: str | None = None          # vertical: Sub Box / Proof Lab / Stack / ...
    series: str | None = None          # e.g. "Sub Box arcs" when content_type=SERIES

    asset_ref: str                     # OneDrive item id / Figma node id
    caption: str = ""
    scheduled_at: str                  # ISO-8601, UTC (planner assigns; see planner.py)

    status: PostStatus = PostStatus.DRAFT
    approved_by: str | None = None     # Slack user id of Paul/Barbara on approve
    published_id: str | None = None    # platform media id once live (idempotency key)
    attempts: int = 0
    last_error: str | None = None
    created_at: str | None = None
