"""Figma client — enumerate library assets from the design file.

Uses settings.figma_access_token + settings.figma_file_key to read nodes and
export image fills. Complements OneDrive as a second library source.
"""

from __future__ import annotations

from loguru import logger


async def list_assets() -> list[dict]:
    """List exportable assets in the Figma file. TODO."""
    logger.info("listing Figma assets from file {}", "<file_key>")
    raise NotImplementedError
