"""Instagram publishing via the Graph API Content Publishing flow.

Two-step publish: (1) create a media container pointing at a PUBLIC url
(supplied by media_host), (2) publish the container. Handles reels, images,
and carousels. Also owns long-lived-token refresh (app_id/app_secret).

Requires the App-Review scope instagram_content_publish — until that clears,
run against a sandbox/test account.
"""

from __future__ import annotations

from loguru import logger


async def refresh_token_if_needed() -> None:
    """Exchange/extend the long-lived token before expiry. TODO."""
    raise NotImplementedError


async def publish(public_media_url: str, caption: str, *, is_video: bool) -> str:
    """Create container → publish. Returns the published media id. TODO."""
    logger.info("IG publish (video={})", is_video)
    raise NotImplementedError
