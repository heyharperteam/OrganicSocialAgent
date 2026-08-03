"""Shared shapes for the content-library index.

Two records:
  - LibraryAsset      — a file discovered in a source (OneDrive/Figma): identity,
                        location, and the metadata needed for change detection.
  - AssetDescription  — the structured categorization the vision pass emits, the
                        thing the recommender matches on. Prose lives in `caption`;
                        the structured fields are what make patterns queryable.

One AssetDescription is persisted per asset as
`data/library/<source>_<asset_id>.json`; `_manifest.json` tracks change state so
re-indexing only pays for new/changed files.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LibraryAsset(BaseModel):
    """A file found in a source library, before (or independent of) description."""
    source: str                      # "onedrive" | "figma"
    asset_id: str                    # OneDrive item id / Figma node id (stable)
    name: str                        # filename / node name
    rel_path: str = ""               # path under the configured base (for humans)
    mime: str = ""                   # e.g. image/jpeg, video/quicktime, application/pdf
    media_kind: str = "image"        # normalized: image | video | pdf
    size: int = 0                    # bytes (change-detection signal)
    last_modified: str = ""          # ISO-8601 (change-detection signal)
    download_url: str = ""           # pre-authenticated URL when the source gives one


class AssetDescription(BaseModel):
    """Structured categorization of one asset — the recommender's match surface."""
    # provenance / linkage
    source: str
    asset_id: str
    name: str
    rel_path: str = ""
    media_kind: str = "image"        # image | video | pdf
    content_sha256: str = ""         # of the source bytes — true content-change key
    frames_analyzed: int = 1         # how many images the vision call actually saw
    indexed_at: str = ""             # ISO-8601 UTC (stamped by the indexer)
    model: str = ""                  # vision model used

    # ── structured features (the queryable pattern surface) ──
    vertical: str = ""               # charms | chains | rings | earrings | bracelets | necklaces | mixed | other
    people: str = "none"             # none | hands_only | model | multiple
    setting: str = ""                # short scene, e.g. "marble flatlay", "beach", "studio white"
    style_tags: list[str] = Field(default_factory=list)   # e.g. ["minimal", "warm_tones", "close_up"]
    dominant_colors: list[str] = Field(default_factory=list)
    text_overlay: bool = False       # is there graphic text burned into the creative?
    detected_text: str = ""          # the on-creative copy/headline, if any (verbatim)
    best_format: list[str] = Field(default_factory=list)  # subset of carousel|reel|story|tiktok_video
    content_type_signal: str = ""    # campaign | ugc | series (feeds the 50/30/20 split)
    suggested_pillar: str = ""       # Sub Box / Proof Lab / Stack / Sports / ... (free)

    # ── prose ──
    caption: str = ""                # human-readable description of the asset

    @field_validator("style_tags", "dominant_colors", "best_format", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        """Tolerate models that return a comma-separated string for an array field
        (Haiku sometimes does this despite the tool schema) → split into a list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v
