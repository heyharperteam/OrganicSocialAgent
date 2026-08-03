"""Vision categorization of one asset → a structured AssetDescription.

Sends the asset's frames (1 for an image, several for a video/PDF) to the vision
model and forces a single `record_asset` tool call, so the output is always the
structured record the recommender matches on — never free prose we'd have to
parse. Same forced-tool_use pattern PPB uses in extractor/deck_analyzer.py.

Runs on the cheap model (settings.anthropic_vision_model, Haiku) — this is bulk
categorization, one call per new/changed asset.
"""

from __future__ import annotations

import base64

import anthropic
from loguru import logger

from organic_social_agent.library.schema import AssetDescription
from organic_social_agent.settings import settings

_TOOL_SCHEMA = {
    "name": "record_asset",
    "description": "Record the structured categorization of one social-media creative asset.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vertical": {
                "type": "string",
                "description": "Primary product category shown, e.g. charms, chains, rings, "
                "earrings, bracelets, necklaces, watches, mixed, or other. Best guess; 'other' if unclear.",
            },
            "people": {
                "type": "string",
                "enum": ["none", "hands_only", "model", "multiple"],
                "description": "Human presence: none (product only), hands_only, a single model, or multiple people.",
            },
            "setting": {
                "type": "string",
                "description": "Short scene description, e.g. 'marble flatlay', 'beach', 'studio white', 'city street'.",
            },
            "style_tags": {
                "type": "array", "items": {"type": "string"},
                "description": "3-6 short lowercase visual-style tags, e.g. 'minimal', 'warm_tones', 'close_up', 'editorial'.",
            },
            "dominant_colors": {
                "type": "array", "items": {"type": "string"},
                "description": "2-4 dominant colors as simple names, e.g. 'gold', 'cream', 'black'.",
            },
            "text_overlay": {
                "type": "boolean",
                "description": "True if there is graphic/marketing text burned into the creative.",
            },
            "detected_text": {
                "type": "string",
                "description": "The on-creative copy/headline copied verbatim, if any. Empty string if none.",
            },
            "best_format": {
                "type": "array",
                "items": {"type": "string", "enum": ["carousel", "reel", "story", "tiktok_video"]},
                "description": "Which post formats this asset best suits. Static single image → carousel/story; "
                "video → reel/tiktok_video. Pick all that genuinely fit.",
            },
            "content_type_signal": {
                "type": "string",
                "enum": ["campaign", "ugc", "series"],
                "description": "campaign = polished editorial/ecommerce; ugc = customer/creator-style casual; "
                "series = recurring format/episode. Best guess from look and feel.",
            },
            "suggested_pillar": {
                "type": "string",
                "description": "Optional themed pillar if evident (e.g. 'Sub Box', 'Proof Lab', 'Stack styling'). Empty if unclear.",
            },
            "caption": {
                "type": "string",
                "description": "One or two sentences plainly describing what the asset shows — subject, styling, mood, setting.",
            },
        },
        "required": ["vertical", "people", "setting", "style_tags", "text_overlay",
                     "best_format", "content_type_signal", "caption"],
    },
}

_SYSTEM_PROMPT = """You categorize a single social-media creative asset for Hey Harper, \
a waterproof-jewelry brand posting on Instagram and TikTok.

You are shown the asset's image(s). For a video or multi-page PDF you get several \
frames/pages from the SAME asset — describe it as ONE creative, not as separate images.

Read out the structured fields as accurately as you can from what is visible. Copy any \
on-creative marketing copy verbatim into detected_text. Use meaning, not guesswork: if a \
field is genuinely unclear, use the neutral/empty value rather than inventing detail.

Call record_asset exactly once with everything you observe."""


def _image_block(jpeg: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode("ascii"),
        },
    }


async def categorize(frames: list[bytes], *, media_kind: str) -> AssetDescription:
    """Run the vision call over an asset's frames → an AssetDescription with the
    feature fields populated (provenance fields are filled in by the indexer).

    Raises RuntimeError if the model returns no tool call, or ValueError if there
    are no frames to look at.
    """
    if not frames:
        raise ValueError("no frames to categorize")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    lead = (
        f"This asset is a {media_kind}. "
        + ("Here are sampled frames from the one video:" if media_kind == "video"
           else "Here are the pages of the one PDF creative:" if media_kind == "pdf"
           else "Here is the image:")
    )
    content: list[dict] = [{"type": "text", "text": lead}]
    content.extend(_image_block(f) for f in frames)

    response = await client.messages.create(
        model=settings.anthropic_vision_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "record_asset"},
        messages=[{"role": "user", "content": content}],
    )

    for block in response.content:
        if block.type == "tool_use":
            desc = AssetDescription(
                source="", asset_id="", name="",   # provenance filled by caller
                media_kind=media_kind,
                frames_analyzed=len(frames),
                model=settings.anthropic_vision_model,
                **block.input,
            )
            logger.info(
                "categorized: vertical={!r} format={} type={!r} ({} frame(s))",
                desc.vertical, desc.best_format, desc.content_type_signal, len(frames),
            )
            return desc

    raise RuntimeError("vision model did not call record_asset")
