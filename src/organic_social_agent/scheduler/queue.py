"""Persistent post queue backed by Postgres (settings.database_url).

CRUD + status transitions over the Post model. Shared between the FastAPI
process (Slack handlers enqueue drafts / flip approvals) and the worker
process (claims due+approved posts). asyncpg connection pool.
"""

from __future__ import annotations

from loguru import logger

from organic_social_agent.scheduler.models import Post, PostStatus


async def init_schema() -> None:
    """Create the posts table if absent. TODO."""
    raise NotImplementedError


async def enqueue_drafts(posts: list[Post]) -> None:
    """Persist proposed posts as DRAFT. TODO."""
    logger.info("enqueue {} draft(s)", len(posts))
    raise NotImplementedError


async def set_status(post_id: str, status: PostStatus) -> None:
    """Advance a post's state (used by approval handlers). TODO."""
    raise NotImplementedError


async def claim_due(now_iso: str) -> list[Post]:
    """Return APPROVED posts whose scheduled_at <= now, mark QUEUED. TODO.

    Must be atomic (SELECT ... FOR UPDATE SKIP LOCKED) so two worker ticks
    never claim the same post — the idempotency guard.
    """
    raise NotImplementedError
