"""TikTok Business API — read the organic KPIs.

Organic analytics require a connected TikTok *Business Account*
(settings.tiktok_business_id) and the Business API scopes. Maps the same
4 Hey Harper KPIs onto TikTok's video/account analytics fields.
"""

from __future__ import annotations

from datetime import date

from loguru import logger


async def fetch_kpis(since: date, until: date) -> dict:
    """Return the 4 KPIs for the window. TODO: call Business API analytics."""
    logger.info("tiktok insights {} → {}", since, until)
    raise NotImplementedError
