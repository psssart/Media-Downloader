"""Trimming for downloads that are not re-encoded.

The clip is cut with stream copy: cheap enough to run without admission, but a
video cut can only start on a keyframe, so the clip may begin up to one GOP
(typically 1-5 s) before the requested start. Compression trims exactly while
it re-encodes and doesn't use this module except for clip_label().
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TRIM_TIMEOUT_SECONDS = 600
THUMBNAIL_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MP4_FAMILY = (".mp4", ".m4a", ".mov")


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def clip_label(start: Optional[float], end: Optional[float]) -> str:
    """File name tag for a clip, e.g. "0m30s-1m15s" (no colons: they break file names)."""
    return f"{_clock(start or 0)}-{_clock(end) if end is not None else 'end'}"


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, timeout=60, check=True,
        ).stdout
        return float(json.loads(out).get("format", {}).get("duration") or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0.0


def trim_media(src: Path, dest: Path, start: Optional[float], end: Optional[float]) -> None:
    """Copy [start, end) of src into dest (same container) without re-encoding."""
    # Seeking past the end isn't an error for ffmpeg: it would copy the last GOP
    total = _probe_duration(src)
    if total and (start or 0) >= total:
        raise ValueError("The trim range is outside the video.")
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if end is not None:
        args += ["-t", f"{end - (start or 0):.3f}"]
    # Shift the leading keyframe to 0 instead of hiding it behind an edit list
    args += ["-map", "0:v?", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    if dest.suffix.lower() in MP4_FAMILY:
        args += ["-movflags", "+faststart"]
    args.append(str(dest))
    try:
        proc = subprocess.run(args, capture_output=True, timeout=TRIM_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ValueError("Trimming timed out.")
    if proc.returncode != 0:
        logger.error(f"ffmpeg trim failed for {src.name}: {proc.stderr.decode(errors='replace')[-600:]}")
        raise ValueError("Trimming failed.")
    if not dest.exists() or _probe_duration(dest) <= 0:
        raise ValueError("The trim range is outside the video.")


def deliver_trimmed(src: Path, work_dir: Path, user_dir: Path,
                    start: Optional[float], end: Optional[float]) -> Path:
    """Trim a download from work_dir into user_dir as "<title> [<clip>]" along with its thumbnail.

    The clip gets its own name so it never overwrites (or is mistaken by
    yt-dlp for) a full download of the same video.
    """
    trimmed = work_dir / f".trimmed{src.suffix}"
    trim_media(src, trimmed, start, end)

    stem = f"{src.stem} [{clip_label(start, end)}]"
    dest = user_dir / f"{stem}{src.suffix}"
    n = 2
    while dest.exists():
        dest = user_dir / f"{stem} ({n}){src.suffix}"
        n += 1
    os.replace(trimmed, dest)
    for thumb in work_dir.iterdir():
        if thumb.stem == src.stem and thumb.suffix.lower() in THUMBNAIL_SUFFIXES:
            os.replace(thumb, dest.with_suffix(thumb.suffix.lower()))
            break
    return dest
