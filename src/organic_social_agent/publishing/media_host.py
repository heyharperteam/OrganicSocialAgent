"""Temporary public staging of media for the Meta publish flow.

Meta fetches media from an anonymous public URL; OneDrive/Figma links are
auth-gated, so we stage each asset in object storage (S3 / Cloudflare R2),
hand Meta the public URL, and tear it down after publish confirms.

    async with staged(asset_bytes, "reel.mp4") as public_url:
        media_id = await meta_client.publish(public_url, caption, is_video=True)
    # object deleted on exit; bucket lifecycle TTL is the backstop.

TikTok never uses this — it uploads bytes directly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from loguru import logger


@asynccontextmanager
async def staged(data: bytes, filename: str):
    """Upload to the bucket, yield a public URL, delete on exit. TODO."""
    logger.info("staging {} ({} bytes)", filename, len(data))
    raise NotImplementedError
    yield  # pragma: no cover
