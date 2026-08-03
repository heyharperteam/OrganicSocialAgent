"""Turn any asset's raw bytes into a list of compressed JPEG frames.

Claude's vision API accepts images, not video or PDF files — so every media kind
is normalized to one or more JPEGs the categorizer can look at:

  - image → 1 downscaled JPEG
  - pdf   → up to `pdf_max_pages` page renders (PyMuPDF, no system binary)
  - video → `video_frame_samples` frames evenly sampled across the clip, plus the
            cover frame (bundled ffmpeg via imageio-ffmpeg, no system install)

All frames are downscaled to `vision_image_max_px` on the long edge and JPEG-
compressed at `vision_jpeg_quality` to keep vision-call tokens (and cost) low.
"""

from __future__ import annotations

import io
import re
import subprocess
import tempfile
from pathlib import Path

from loguru import logger
from PIL import Image

from organic_social_agent.settings import settings


def _compress(img: Image.Image) -> bytes:
    """Downscale to the configured long edge and JPEG-encode."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    max_px = settings.vision_image_max_px
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=settings.vision_jpeg_quality)
    return buf.getvalue()


def _frames_from_image(data: bytes) -> list[bytes]:
    return [_compress(Image.open(io.BytesIO(data)))]


def _frames_from_pdf(data: bytes) -> list[bytes]:
    """Rasterize the first `pdf_max_pages` pages to JPEGs via PyMuPDF.

    The zoom is derived from each page's own dimensions to target ~1.5x the
    vision long-edge (headroom for legible text, then _compress trims to policy).
    Rendering to a bounded size — rather than a fixed matrix — avoids MuPDF's
    pixmap size limit on large-canvas creatives (these are ~23MB full-bleed PDFs).
    """
    import fitz  # PyMuPDF

    target_px = settings.vision_image_max_px * 1.5
    frames: list[bytes] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        n = min(len(doc), settings.pdf_max_pages)
        for i in range(n):
            page = doc[i]
            long_edge_pt = max(page.rect.width, page.rect.height) or 1.0
            zoom = min(2.0, target_px / long_edge_pt)   # never upscale beyond 2x
            zoom = max(zoom, 0.05)                        # and never zero
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            frames.append(_compress(Image.open(io.BytesIO(pix.tobytes("png")))))
        if len(doc) > n:
            logger.info("PDF has {} pages; sampled first {}", len(doc), n)
    return frames


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")


def _video_duration(ffmpeg: str, path: str) -> float | None:
    """Parse duration (seconds) from ffmpeg's stderr banner. None if unknown."""
    proc = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True)
    m = _DURATION_RE.search(proc.stderr)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def _grab_frame(ffmpeg: str, path: str, ts: float) -> bytes | None:
    """Extract a single frame at timestamp `ts` (seconds) as JPEG bytes."""
    proc = subprocess.run(
        [ffmpeg, "-ss", f"{ts:.2f}", "-i", path, "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return _compress(Image.open(io.BytesIO(proc.stdout)))


def _frames_from_video(data: bytes) -> list[bytes]:
    """Sample N frames evenly across the clip (+ the cover frame at t=0)."""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    n = max(1, settings.video_frame_samples)
    with tempfile.TemporaryDirectory() as tmp:
        vid = str(Path(tmp) / "clip")
        Path(vid).write_bytes(data)
        duration = _video_duration(ffmpeg, vid)
        if duration and duration > 0:
            # Evenly spaced timestamps, biased slightly inward to avoid black
            # first/last frames; cover frame (t≈0) always included first.
            step = duration / (n + 1)
            timestamps = [0.0] + [step * (i + 1) for i in range(n)]
        else:
            # Duration unknown — fall back to fixed 2s spacing.
            timestamps = [i * 2.0 for i in range(n + 1)]
        frames = [f for ts in timestamps if (f := _grab_frame(ffmpeg, vid, ts))]
    if not frames:
        logger.warning("No frames extracted from video — vision pass will be skipped")
    return frames


def frames_for(media_kind: str, data: bytes) -> list[bytes]:
    """Dispatch on media kind → list of JPEG frames for the vision call."""
    if media_kind == "image":
        return _frames_from_image(data)
    if media_kind == "pdf":
        return _frames_from_pdf(data)
    if media_kind == "video":
        return _frames_from_video(data)
    raise ValueError(f"unsupported media_kind {media_kind!r}")
