"""Microsoft Graph client — read the OneDrive asset library.

Ported from Product Page Builder's onedrive/graph_client.py: MSAL
client-credentials (app-only) auth against settings.ms_*, then list/download
drive items over httpx. Read-only — the agent indexes assets, never writes to
OneDrive.

Where PPB searched for one SKU's folder, the library indexer instead **walks a
whole subtree** under a configured base path (ONEDRIVE_ASSETS_BASE_PATH, e.g.
"Documents/2026/H1 - 2026/Summer Sale 2026/02 - Creative Comissions") and yields
every postable file it finds (images, videos, PDFs).
"""

from __future__ import annotations

import re

import httpx
import msal
from loguru import logger

from organic_social_agent.library.schema import LibraryAsset
from organic_social_agent.settings import settings

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]

# MIME prefixes / types we treat as postable creative.
_IMAGE_PREFIX = "image/"
_VIDEO_PREFIX = "video/"
_PDF_MIME = "application/pdf"


def _norm(name: str) -> str:
    """Whitespace-insensitive, case-insensitive key for tolerant folder matching:
    lets 'H1 - 2026' match the drive's actual 'H1- 2026' (PPB pattern)."""
    return re.sub(r"\s+", "", name).lower()


def _media_kind(mime: str) -> str | None:
    if mime.startswith(_IMAGE_PREFIX):
        return "image"
    if mime.startswith(_VIDEO_PREFIX):
        return "video"
    if mime == _PDF_MIME:
        return "pdf"
    return None


class LibraryGraphClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        drive_id: str | None = None,
    ):
        self.drive_id = drive_id or settings.ms_drive_id_assets
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id or settings.ms_client_id,
            client_credential=client_secret or settings.ms_client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id or settings.ms_tenant_id}",
        )

    # ── auth ──────────────────────────────────────────────────────────────────
    def _get_token(self) -> str:
        result = self._app.acquire_token_silent(_SCOPE, account=None)
        if not result:
            result = self._app.acquire_token_for_client(scopes=_SCOPE)
        if "access_token" not in result:
            raise RuntimeError(f"Graph auth failed: {result.get('error_description')}")
        return result["access_token"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ── low-level HTTP ──────────────────────────────────────────────────────────
    async def _get(self, client: httpx.AsyncClient, url: str) -> dict:
        full = url if url.startswith("http") else f"{_GRAPH_BASE}{url}"
        resp = await client.get(full, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def _list_children(self, client: httpx.AsyncClient, endpoint: str) -> list[dict]:
        """List all children at a Graph children-endpoint, following paging."""
        items: list[dict] = []
        url = endpoint
        while url:
            data = await self._get(client, url)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink", "")
        return items

    def _children_of_path(self, path: str) -> str:
        path = path.strip("/")
        if not path:
            return f"/drives/{self.drive_id}/root/children"
        return f"/drives/{self.drive_id}/root:/{path}:/children"

    def _children_of_item(self, item_id: str) -> str:
        return f"/drives/{self.drive_id}/items/{item_id}/children"

    # ── path resolution (tolerant) ────────────────────────────────────────────
    async def _resolve_folder_id(self, client: httpx.AsyncClient, path: str) -> str | None:
        """Resolve a slash path to a folder's item id by walking segment-by-segment
        with whitespace/case-normalized matching. Returns None for the drive root
        (empty path) and raises LookupError if a segment can't be matched.

        Tolerates the drive's inconsistent spacing ('H1- 2026' vs 'H1 - 2026') and
        strips a leading 'Documents' segment (the drive root already IS that library).
        """
        segments = [s for s in path.strip("/").split("/") if s]
        if segments and _norm(segments[0]) == "documents":
            segments = segments[1:]

        current_id: str | None = None  # None = drive root
        for seg in segments:
            endpoint = (
                f"/drives/{self.drive_id}/root/children"
                if current_id is None
                else self._children_of_item(current_id)
            )
            children = await self._list_children(client, endpoint)
            match = next(
                (c for c in children if "folder" in c and _norm(c["name"]) == _norm(seg)),
                None,
            )
            if match is None:
                avail = [c["name"] for c in children if "folder" in c]
                raise LookupError(f"folder segment {seg!r} not found; available: {avail}")
            current_id = match["id"]
        return current_id

    async def _root_endpoint_for(self, client: httpx.AsyncClient, path: str) -> str:
        """children-endpoint for the folder at `path` (resolved tolerantly)."""
        folder_id = await self._resolve_folder_id(client, path)
        return (
            f"/drives/{self.drive_id}/root/children"
            if folder_id is None
            else self._children_of_item(folder_id)
        )

    # ── walking ─────────────────────────────────────────────────────────────────
    def _to_asset(self, child: dict, rel_path: str) -> LibraryAsset | None:
        """Map a Graph driveItem to a LibraryAsset, or None if not postable media."""
        file_facet = child.get("file")
        if not file_facet:
            return None
        mime = file_facet.get("mimeType", "")
        kind = _media_kind(mime)
        if kind is None:
            return None
        return LibraryAsset(
            source="onedrive",
            asset_id=child["id"],
            name=child["name"],
            rel_path=f"{rel_path}/{child['name']}".lstrip("/"),
            mime=mime,
            media_kind=kind,
            size=int(child.get("size", 0)),
            last_modified=child.get("lastModifiedDateTime", ""),
            download_url=child.get("@microsoft.graph.downloadUrl", ""),
        )

    async def walk(
        self,
        base_path: str = "",
        *,
        recursive: bool = True,
        _endpoint: str | None = None,
        _rel: str = "",
    ) -> list[LibraryAsset]:
        """Recursively list every postable file under `base_path`.

        Folders are descended into (when recursive); image/video/pdf files are
        collected. Returns a flat, path-labeled list. `base_path` is resolved
        tolerantly (spacing/case-insensitive, 'Documents' prefix optional).
        """
        async with httpx.AsyncClient(timeout=120.0) as client:
            endpoint = _endpoint or await self._root_endpoint_for(client, base_path)
            return await self._walk(client, base_path, recursive, endpoint, _rel)

    async def _walk(
        self,
        client: httpx.AsyncClient,
        base_path: str,
        recursive: bool,
        endpoint: str | None,
        rel: str,
    ) -> list[LibraryAsset]:
        endpoint = endpoint or self._children_of_path(base_path)
        rel = rel or ""
        assets: list[LibraryAsset] = []
        children = await self._list_children(client, endpoint)
        for child in children:
            if "folder" in child:
                if recursive:
                    sub_rel = f"{rel}/{child['name']}".lstrip("/")
                    assets.extend(
                        await self._walk(
                            client, base_path, True,
                            self._children_of_item(child["id"]), sub_rel,
                        )
                    )
                continue
            asset = self._to_asset(child, rel)
            if asset:
                assets.append(asset)
        return assets

    async def list_child_folders(self, base_path: str = "") -> list[str]:
        """Names of immediate subfolders under base_path (for scoping a run)."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            endpoint = await self._root_endpoint_for(client, base_path)
            children = await self._list_children(client, endpoint)
            return sorted(c["name"] for c in children if "folder" in c)

    # ── download ─────────────────────────────────────────────────────────────────
    async def download_bytes(self, asset: LibraryAsset) -> bytes:
        """Fetch an asset's raw bytes — via its pre-authenticated download URL if
        present, else the authenticated /content endpoint."""
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            if asset.download_url:
                resp = await client.get(asset.download_url)
                resp.raise_for_status()
                return resp.content
            resp = await client.get(
                f"{_GRAPH_BASE}/drives/{self.drive_id}/items/{asset.asset_id}/content",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.content


async def list_assets(base_path: str = "") -> list[LibraryAsset]:
    """Convenience: walk the default drive under `base_path`."""
    client = LibraryGraphClient()
    assets = await client.walk(base_path or settings.onedrive_assets_base_path)
    logger.info("OneDrive: found {} postable asset(s) under {!r}", len(assets), base_path)
    return assets
