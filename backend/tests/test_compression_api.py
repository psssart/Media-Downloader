"""API and TaskManager wiring (yt-dlp is stubbed; ffmpeg runs for real)."""

import shutil
import statistics
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import FormatInfo, MediaInfo, TERMINAL_STATUSES
from app.services.compressor import compressor
from app.services.ytdlp_wrapper import ytdlp_wrapper
from app.services.file_manager import file_manager
from tests.conftest import make_video, requires_ffmpeg

CLIENT = str(uuid.uuid4())
HEADERS = {"X-Client-ID": CLIENT, "X-Real-IP": "203.0.113.7"}


def media(duration=10, formats=None):
    return MediaInfo(id="abc", title="Stub video", webpage_url="https://example.com/watch?v=abc",
                     extractor="youtube", duration=duration, formats=formats or [])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ytdlp_wrapper, "extract_info_cached", lambda url, refresh=False: media())
    with TestClient(app) as c:
        yield c
    for job in list(compressor.jobs.values()):
        compressor.cancel(job.task.task_id)
    deadline = time.monotonic() + 30
    while compressor.jobs and time.monotonic() < deadline:  # per-user limit is 1
        time.sleep(0.05)


def test_presets_hide_hidden_entries(client):
    data = client.get("/api/downloads/compression/presets").json()
    ids = [p["id"] for p in data["presets"]]
    assert "discord" in ids and "telegram" not in ids
    discord = next(p for p in data["presets"] if p["id"] == "discord")
    assert discord["target_size_mb"] == 20
    assert data["max_total_wait_seconds"] == 1800


def test_estimate_ok(client):
    r = client.post("/api/downloads/compression/estimate", headers=HEADERS,
                    json={"url": "https://example.com/watch?v=abc", "compression": {"preset_id": "discord"}})
    body = r.json()
    assert r.status_code == 200 and body["admitted"], body
    assert body["target_mb"] == 20 and body["alternatives"] == []


def test_start_rejected_returns_structured_409(client, monkeypatch):
    monkeypatch.setattr(ytdlp_wrapper, "extract_info_cached", lambda url, refresh=False: media(duration=3000))
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": "https://example.com/watch?v=abc", "compression": {"target_size_mb": 50}})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "too_long" and detail["message"]
    # without a format list the worst case (1080p, unknown codec) is assumed, so 20 min
    # alone is still too slow: every alternative trims and adds something on top
    assert detail["alternatives"]
    for alt in detail["alternatives"]:
        assert alt["options"]["trim_end"] <= 1200 and alt["estimate"]["admitted"]
    assert not compressor.jobs  # nothing reserved


def test_unknown_duration_is_explained(client, monkeypatch):
    monkeypatch.setattr(ytdlp_wrapper, "extract_info_cached", lambda url, refresh=False: media(duration=None))
    body = client.post("/api/downloads/compression/estimate", headers=HEADERS,
                       json={"url": "https://example.com/live", "compression": {"target_size_mb": 10}}).json()
    assert not body["admitted"] and body["code"] == "unknown_duration"


def test_compression_rejected_for_audio_only(client):
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": "https://example.com/watch?v=abc", "audio_only": True,
                          "compression": {"target_size_mb": 10}})
    assert r.status_code == 400


def test_unknown_preset(client):
    body = client.post("/api/downloads/compression/estimate", headers=HEADERS,
                       json={"url": "https://example.com/x", "compression": {"preset_id": "nope"}}).json()
    assert body["code"] == "invalid_preset"


def test_stats_endpoint(client):
    stats = client.get("/api/downloads/compression/stats").json()
    assert {"perf", "queue", "estimate_ratio", "admissions"} <= stats.keys()


def test_cleanup_skips_work_dir(tmp_path):
    work = file_manager.downloads_dir / ".work" / "keepme"
    work.mkdir(parents=True, exist_ok=True)
    (work / "input.mp4").write_bytes(b"x")
    file_manager.cleanup_old_files(retention_hours=-1)  # everything is "old"
    assert (work / "input.mp4").exists()
    shutil.rmtree(work)


# ---------------------------------------------------------------- end to end with a stubbed download

@pytest.fixture
def stub_download(monkeypatch, tmp_path_factory):
    video = make_video(tmp_path_factory.mktemp("src") / "Stub video.mp4", seconds=8)
    calls = {}

    def fake_download(url, output_path, format_spec=None, progress_hook=None, convert_to_mp4=True,
                      max_filesize=None):
        calls.update(format_spec=format_spec, convert_to_mp4=convert_to_mp4, output_path=output_path)
        for i in range(1, 11):
            if progress_hook:
                progress_hook({"status": "downloading", "downloaded_bytes": i, "total_bytes": 10})
            time.sleep(calls.get("delay", 0))
        dest = Path(output_path) / video.name
        shutil.copy(video, dest)
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


@requires_ffmpeg
def test_end_to_end_compression(client, stub_download, monkeypatch):
    formats = [FormatInfo(format_id="136", ext="mp4", resolution="1280x720", vcodec="avc1", acodec="none",
                          has_audio=False),
               FormatInfo(format_id="140", ext="m4a", vcodec="none", acodec="mp4a", abr=128,
                          has_video=False, has_audio=True)]
    monkeypatch.setattr(ytdlp_wrapper, "extract_info_cached",
                        lambda url, refresh=False: media(duration=8, formats=formats))
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": "https://example.com/watch?v=abc", "quality": "720p",
                          "compression": {"target_size_mb": 0.4}})
    assert r.status_code == 200, r.text
    task = wait_task(client, r.json()["task_id"])
    assert task["status"] == "completed", task
    assert stub_download["format_spec"].startswith("136+140/")  # planner's exact formats first
    assert stub_download["convert_to_mp4"] is False
    assert Path(stub_download["output_path"]).parent.name == ".work"
    res = task["compression_result"]
    assert res["actual_mb"] <= res["target_mb"] == 0.4
    files = client.get("/api/downloads/files", headers=HEADERS).json()
    assert any(f["filename"] == task["filename"] for f in files)
    assert not any(".work" in f["filepath"] for f in files)


@requires_ffmpeg
def test_cancel_during_download(client, stub_download):
    stub_download["delay"] = 0.3
    r = client.post("/api/downloads/start", headers=HEADERS,
                    json={"url": "https://example.com/watch?v=abc", "compression": {"target_size_mb": 1}})
    task_id = r.json()["task_id"]
    time.sleep(0.5)
    assert client.post(f"/api/downloads/tasks/{task_id}/cancel", headers=HEADERS).status_code == 200
    task = wait_task(client, task_id)
    assert task["status"] == "cancelled"
    assert not (compressor.work_dir / task_id).exists()


@requires_ffmpeg
def test_api_stays_responsive_while_encoding(client, tmp_path):
    """Smoke check; the real check is deploy/loadtest on the target server."""
    from app.models import CompressionOptions, DownloadTask
    from datetime import datetime
    video = make_video(tmp_path / "Busy.mp4", seconds=30, size="1280x720", vbitrate="4M")
    task = DownloadTask(task_id="busy0001", client_id=CLIENT, url="https://e/x", created_at=datetime.now())
    job = compressor.admit(task, media(duration=30), None, CompressionOptions(target_size_mb=2), CLIENT, "198.51.100.1")
    shutil.copy(video, job.work_dir / video.name)
    compressor.submit(task.task_id, job.work_dir / video.name)
    deadline = time.monotonic() + 30
    while task.status != "compressing" and time.monotonic() < deadline:
        time.sleep(0.05)
    latencies = []
    for _ in range(20):
        start = time.monotonic()
        assert client.get("/api/health").status_code == 200
        latencies.append(time.monotonic() - start)
    compressor.cancel(task.task_id)
    assert task.status != "failed"
    assert statistics.median(latencies) < 0.1 and max(latencies) < 1
