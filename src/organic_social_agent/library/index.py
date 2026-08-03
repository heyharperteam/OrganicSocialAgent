"""Build & maintain the content-library index from OneDrive (+ Figma later).

C1 of the curation engine. Walks the configured asset subtree, and for each
new/changed file: downloads it, normalizes it to frames (image/video/PDF), runs
the vision categorizer, and writes ONE description file per asset:

    {output_dir}/library/<source>_<asset_id>.json     # structured record
    {output_dir}/library/_manifest.json               # change-detection state
    {output_dir}/library/library.md                    # human-readable roll-up

Self-updating & incremental: a run reconciles the source against the manifest —
unchanged files are skipped (no vision cost), new/changed files are (re)indexed,
and on a FULL run (no filters) files gone from the source are marked archived.
So dropping new media into the OneDrive folder just means its description appears
on the next run, at ~cents.

    uv run --system-certs index-library                 # full run
    uv run --system-certs index-library --subfolder Bbesdoingbits --ext pdf
    uv run --system-certs index-library --list-folders  # just list subfolders
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import truststore
from loguru import logger

from organic_social_agent.library import categorize, media_probe, render
from organic_social_agent.library.graph_client import LibraryGraphClient
from organic_social_agent.library.schema import AssetDescription, LibraryAsset
from organic_social_agent.settings import settings

truststore.inject_into_ssl()  # corporate-CA SSL (PPB pattern)


def _library_dir() -> Path:
    d = Path(settings.output_dir) / "library"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return _library_dir() / "_manifest.json"


def _record_path(asset: LibraryAsset) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{asset.source}_{asset.asset_id}").strip("_")
    return _library_dir() / f"{safe}.json"


def _load_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("manifest unreadable — starting fresh")
    return {}


def _save_manifest(manifest: dict) -> None:
    _manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _unchanged(entry: dict | None, asset: LibraryAsset) -> bool:
    """True if the manifest says this asset is unchanged since last index."""
    return bool(
        entry
        and entry.get("last_modified") == asset.last_modified
        and entry.get("size") == asset.size
        and entry.get("status") != "archived"
        and Path(entry.get("record_file", "")).exists()
    )


async def _index_one(client: LibraryGraphClient, asset: LibraryAsset) -> AssetDescription:
    """Download, frame, categorize, and persist one asset's description file."""
    data = await client.download_bytes(asset)
    sha = hashlib.sha256(data).hexdigest()
    frames = media_probe.frames_for(asset.media_kind, data)
    desc = await categorize.categorize(frames, media_kind=asset.media_kind)
    # fill provenance the categorizer left blank
    desc.source = asset.source
    desc.asset_id = asset.asset_id
    desc.name = asset.name
    desc.rel_path = asset.rel_path
    desc.content_sha256 = sha
    desc.indexed_at = datetime.now(timezone.utc).isoformat()
    _record_path(asset).write_text(desc.model_dump_json(indent=2), encoding="utf-8")
    return desc


async def build_index(
    *,
    subfolder: str | None = None,
    exts: list[str] | None = None,
    limit: int | None = None,
) -> list[AssetDescription]:
    """Reconcile the OneDrive subtree with the on-disk index.

    subfolder — restrict to one immediate subfolder of the base path.
    exts      — restrict to these extensions (e.g. ["pdf"]).
    limit     — cap number of assets processed (for cheap test runs).

    A run is "full" only when no filter is applied; archival reconciliation runs
    only then (so a scoped test never archives everything else).
    """
    client = LibraryGraphClient()
    base = settings.onedrive_assets_base_path
    walk_path = f"{base}/{subfolder}" if subfolder else base

    assets = await client.walk(walk_path)
    if exts:
        wanted = {e.lower().lstrip(".") for e in exts}
        assets = [a for a in assets if a.name.rsplit(".", 1)[-1].lower() in wanted]
    assets.sort(key=lambda a: a.rel_path.lower())
    if limit:
        assets = assets[:limit]

    logger.info("reconciling {} asset(s) under {!r}", len(assets), walk_path)

    manifest = _load_manifest()
    is_full_run = subfolder is None and not exts and not limit
    seen_ids: set[str] = set()
    results: list[AssetDescription] = []
    new_or_changed = skipped = failed = 0

    for asset in assets:
        seen_ids.add(asset.asset_id)
        entry = manifest.get(asset.asset_id)
        if _unchanged(entry, asset):
            skipped += 1
            continue
        try:
            desc = await _index_one(client, asset)
        except Exception as exc:  # keep going; one bad asset shouldn't stop the run
            failed += 1
            logger.error("failed to index {!r}: {!r}", asset.rel_path, exc)
            continue
        manifest[asset.asset_id] = {
            "name": asset.name,
            "rel_path": asset.rel_path,
            "last_modified": asset.last_modified,
            "size": asset.size,
            "sha256": desc.content_sha256,
            "record_file": str(_record_path(asset)),
            "status": "active",
            "indexed_at": desc.indexed_at,
        }
        results.append(desc)
        new_or_changed += 1

    if is_full_run:
        for asset_id, entry in manifest.items():
            if asset_id not in seen_ids and entry.get("status") != "archived":
                entry["status"] = "archived"
                logger.info("archived (gone from source): {!r}", entry.get("rel_path"))

    _save_manifest(manifest)
    _render_outputs(manifest)
    logger.success(
        "index done: {} new/changed, {} unchanged, {} failed",
        new_or_changed, skipped, failed,
    )
    return results


def _active_descriptions(manifest: dict) -> list[AssetDescription]:
    """Load every active asset's AssetDescription from its record file."""
    out: list[AssetDescription] = []
    active = [e for e in manifest.values() if e.get("status") == "active"]
    for entry in sorted(active, key=lambda e: e.get("rel_path", "")):
        rf = Path(entry.get("record_file", ""))
        if not rf.exists():
            continue
        try:
            out.append(AssetDescription.model_validate_json(rf.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _render_outputs(manifest: dict) -> None:
    """Regenerate library.md + library.html from the current manifest/records."""
    records = _active_descriptions(manifest)
    _render_markdown(records)
    html = render.render_html(
        records,
        base_path=settings.onedrive_assets_base_path,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    (_library_dir() / "library.html").write_text(html, encoding="utf-8")


def _render_markdown(records: list[AssetDescription]) -> None:
    """Render a human-readable roll-up from active description records."""
    lines = ["# Content library index", ""]
    lines.append(f"_{len(records)} active asset(s). Generated from per-asset records._\n")
    for d in records:
        lines += [
            f"## {d.name}",
            f"- **path**: `{d.rel_path}`",
            f"- **kind**: {d.media_kind} · **vertical**: {d.vertical} · **type**: {d.content_type_signal}",
            f"- **best format**: {', '.join(d.best_format) or '—'}",
            f"- **people**: {d.people} · **setting**: {d.setting}",
            f"- **style**: {', '.join(d.style_tags) or '—'}",
            f"- **text overlay**: {d.text_overlay}" + (f" — “{d.detected_text}”" if d.detected_text else ""),
            f"- {d.caption}",
            "",
        ]
    (_library_dir() / "library.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/refresh the content-library index.")
    parser.add_argument("--subfolder", help="restrict to one immediate subfolder of the base path")
    parser.add_argument("--ext", action="append", help="restrict to extension(s), e.g. --ext pdf")
    parser.add_argument("--limit", type=int, help="cap assets processed (cheap test runs)")
    parser.add_argument("--list-folders", action="store_true", help="just list subfolders and exit")
    parser.add_argument("--render-only", action="store_true",
                        help="regenerate library.md + library.html from existing records (no OneDrive/vision)")
    args = parser.parse_args()

    if args.render_only:
        _render_outputs(_load_manifest())
        print(f"Rendered {_library_dir()/'library.html'}")
        return

    if args.list_folders:
        client = LibraryGraphClient()
        folders = asyncio.run(client.list_child_folders(settings.onedrive_assets_base_path))
        print(f"Subfolders under {settings.onedrive_assets_base_path!r}:")
        for f in folders:
            print(f"  - {f}")
        return

    results = asyncio.run(
        build_index(subfolder=args.subfolder, exts=args.ext, limit=args.limit)
    )
    print(f"\nIndexed {len(results)} new/changed asset(s). See {_library_dir()/'library.md'}")


if __name__ == "__main__":
    main()
