"""Scheduler worker — fires due, approved posts.

APScheduler loop (default every 60s):
  1. claim_due() → APPROVED posts past their scheduled time (atomic)
  2. for each: fetch asset (OneDrive) → publish
       - Instagram: media_host.staged(...) → meta_client.publish(...)
       - TikTok:    tiktok_client.publish(bytes)
  3. on success → mark PUBLISHED + store published_id (never double-post)
     on failure → increment attempts, backoff, or mark FAILED
  4. report result to Slack

Runs as its own Railway process (separate from the FastAPI web process).

    uv run --system-certs worker
"""

from __future__ import annotations

from loguru import logger


async def tick() -> None:
    """One scheduler pass. TODO: claim_due → publish → report."""
    logger.info("scheduler tick")
    raise NotImplementedError


def main() -> None:
    """Start the APScheduler loop. TODO."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
