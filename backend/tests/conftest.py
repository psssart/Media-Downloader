import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# Settings are created at import time and create their directories, so point
# them somewhere writable before anything from `app` is imported.
_ROOT = Path(tempfile.mkdtemp(prefix="md-tests-"))
os.environ.setdefault("MD_DOWNLOADS_DIR", str(_ROOT / "downloads"))
os.environ.setdefault("MD_COOKIES_DIR", str(_ROOT / "cookies"))
# No startup calibration in tests unless a test asks for it
os.environ.setdefault("MD_COMPRESSION_THROUGHPUT_OVERRIDE", "50000000")

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def make_video(path: Path, seconds: float = 10, size: str = "640x360", fps: int = 30,
               audio: bool = True, vbitrate: str = "3M") -> Path:
    """Synthetic h264/aac mp4 (noise keeps it hard to compress)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
           ]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-t", str(seconds), "-vf", "noise=alls=40:allf=t", "-c:v", "libx264", "-preset", "ultrafast",
            "-b:v", vbitrate, "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]
    cmd += ["-shortest", str(path)] if audio else [str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    return make_video(tmp_path_factory.mktemp("media") / "Sample clip.mp4")
