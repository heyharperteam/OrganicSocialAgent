"""Weekly-schedule recommender.

Proposes a week of posts from the existing library index, honoring:
  - content split      50% campaign / 30% UGC / 20% series
  - cadence            4x Instagram, every-other-day TikTok
  - format logic       carousel = engagement, reel = education, story = CTA
  - KPI history        favor content types that drove the 4 KPIs before
  - retail journey      keep profile-credibility posts (proof, best sellers,
                       reviews, lifetime guarantee) in rotation
Claude drafts captions on-brand (Harperverse tone / banned words skill) for
EXISTING assets only. Output is a list of draft posts handed to the approval
flow — nothing is queued until a human approves.

    uv run --system-certs propose-week
"""

from __future__ import annotations

from loguru import logger


async def propose_week() -> list[dict]:
    """Return a week of DRAFT posts (asset + caption + target time). TODO."""
    logger.info("proposing weekly schedule")
    raise NotImplementedError


def main() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
