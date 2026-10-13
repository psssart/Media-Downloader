"""Trim on its own (stream copy) and together with compression (yt-dlp is stubbed)."""

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import MediaInfo, TERMINAL_STATUSES
from app.services.compressor import compressor
from app.services.trimmer import clip_label
from app.services.ytdlp_wrapper import ytdlp_wrapper
from tests.conftest import HAS_FFMPEG, requires_ffmpeg

CLIENT = str(uuid.uuid4())
HEADERS = {"X-Client-ID": CLIENT, "X-Real-IP": "203.0.113.9"}
URL = "https://example.com/watch?v=trim"


def media(duration=8):
    return MediaInfo(id="trim", title="Stub video", webpage_url=URL, extractor="youtube",
                     duration=duration, formats=[])


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
                         capture_output=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ytdlp_wrapper, "extract_info_cached", lambda url, refresh=False: media())
    monkeypatch.setattr(ytdlp_wrapper, "extract_info", lambda url: media())
    with TestClient(app) as c:
        yield c
    for job in list(compressor.jobs.values()):
        compressor.cancel(job.task.task_id)
    deadline = time.monotonic() + 30
    while compressor.jobs and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.fixture
def stub_download(monkeypatch, tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    src = tmp_path_factory.mktemp("trim-src")
    # Keyframe every second so stream copy can cut close to the requested start
    video = src / "Stub video.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
                   check=True)
    calls = {}

    def fake_download(url, output_path, format_spec=None, progress_hook=None, convert_to_mp4=True,
                      max_filesize=None):
        calls["output_path"] = Path(output_path)
        dest = Path(output_path) / video.name
        shutil.copy(video, dest)
        dest.with_suffix(".jpg").write_bytes(b"\xff\xd8thumb")
        if progress_hook:
            progress_hook({"status": "finished"})
        return str(dest)

    monkeypatch.setattr(ytdlp_wrapper, "download", fake_download)
    return calls


def wait_task(client, task_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = client.get(f"/api/downloads/tasks/{task_id}", headers=HEADERS).json()
        if t["status"] in [s.value for s in TERMINAL_STATUSES]:
            return t
        time.sleep(0.1)
    raise AssertionError("task did not finish")


def user_file(name: str) -> Path:
    return compressor.cfg.downloads_dir / CLIENT / name


def test_clip_label():
    assert clip_label(30, 75) == "0m30s-1m15s"
    assert clip_label(None, 5) == "0m00s-0m05s"
    assert clip_label(3723, None) == "1h02m03s-end"


def test_trim_rejected_for_images(client):
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": URL, "media_type": "image", "trim_end": 5})
    assert r.status_code == 400


def test_trim_end_must_follow_start(client):
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": URL, "trim_start": 5, "trim_end": 5})
    assert r.status_code == 422


@requires_ffmpeg
def test_plain_trim(client, stub_download):
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": URL, "trim_start": 2, "trim_end": 5})
    assert r.status_code == 200, r.text
    task = wait_task(client, r.json()["task_id"])
    assert task["status"] == "completed", task
    assert task["filename"] == "Stub video [0m02s-0m05s].mp4"
    out = user_file(task["filename"])
    assert 2.9 <= duration_of(out) <= 4.1  # keyframe every second
    assert out.with_suffix(".jpg").exists()  # thumbnail follows the clip
    # Downloaded aside, nothing but the clip reaches the user
    assert stub_download["output_path"].parent == compressor.work_dir
    assert not stub_download["output_path"].exists()
    assert not user_file("Stub video.mp4").exists()
    files = client.get("/api/downloads/files", headers=HEADERS).json()
    assert [f["filename"] for f in files if f["filename"].startswith("Stub video")] == [task["filename"]]


@requires_ffmpeg
def test_trim_outside_video_fails(client, stub_download):
    r = client.post("/api/downloads/start", headers=HEADERS, json={"url": URL, "trim_start": 60})
    task = wait_task(client, r.json()["task_id"])
    assert task["status"] == "failed" and "outside" in task["error"]
    assert not stub_download["output_path"].exists()


@requires_ffmpeg
def test_trim_with_compression_is_cut_by_the_encoder(client, stub_download):
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": URL, "trim_start": 1, "trim_end": 4,
                          "compression": {"target_size_mb": 5}})
    assert r.status_code == 200, r.text
    task = wait_task(client, r.json()["task_id"])
    assert task["status"] == "completed", task
    assert task["filename"] == "Stub video [5MB] [0m01s-0m04s].mp4"
    # Re-encoded, so exact; not trimmed a second time afterwards
    assert duration_of(user_file(task["filename"])) == pytest.approx(3, abs=0.15)
    assert not task["compression_result"]["passthrough"]


def test_top_level_trim_wins_over_compression_trim(client, monkeypatch):
    """Admission sees the same range the download will use."""
    monkeypatch.setattr(ytdlp_wrapper, "extract_info_cached", lambda url, refresh=False: media(duration=3000))
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": URL, "trim_end": 2500,
                          "compression": {"target_size_mb": 50, "trim_end": 60}})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "too_long"
    assert all(alt["options"]["trim_end"] <= 1200 for alt in detail["alternatives"])
