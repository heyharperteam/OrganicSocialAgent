"""TikTok publishing via the Content Posting API.

Unlike Meta, TikTok accepts a DIRECT chunked binary upload (FILE_UPLOAD) — no
public hosting needed. Flow: init upload → PUT chunks → publish (Direct Post).
Owns OAuth access/refresh-token rotation.

"Direct Post" requires the app to have passed TikTok's audit; before that, only
draft/inbox posting is available (usable for end-to-end testing).
"""

from __future__ import annotations

from loguru import logger


async def refresh_token_if_needed() -> None:
    """Rotate the ~24h access token using the refresh token. TODO."""
    raise NotImplementedError


async def publish(media_bytes: bytes, caption: str) -> str:
    """Init → upload chunks → Direct Post. Returns publish id. TODO."""
    logger.info("tiktok publish ({} bytes)", len(media_bytes))
    raise NotImplementedError
