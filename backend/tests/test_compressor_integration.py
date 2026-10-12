"""Real ffmpeg runs on small synthetic videos (run inside the Docker image)."""

import os
import shutil
import signal
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.models import CompressionOptions, DownloadTask, MediaInfo, TaskStatus, TERMINAL_STATUSES
from app.services.compression_calibrator import ThroughputModel
from app.services.compressor import Compressor, probe
from tests.conftest import make_video, requires_ffmpeg

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def long_video(tmp_path_factory) -> Path:
    """Long enough that encoding is still running when a test interferes."""
    return make_video(tmp_path_factory.mktemp("media") / "Long clip.mp4",
                      seconds=40, size="1280x720", vbitrate="4M")


def make_compressor(tmp_path, **kw) -> Compressor:
    kw.setdefault("compression_throughput_override", 50e6)
    cfg = Settings(downloads_dir=tmp_path / "downloads", cookies_dir=tmp_path / "cookies",
                   compression_min_free_disk_mb=1, compression_min_free_ram_mb=1, **kw)
    cfg.downloads_dir.mkdir(parents=True, exist_ok=True)
    comp = Compressor(cfg=cfg, model=ThroughputModel(state_file=tmp_path / "cal.json", cfg=cfg))
    comp.start(calibrate=False)
    return comp


@pytest.fixture
def comp(tmp_path):
    c = make_compressor(tmp_path)
    yield c
    c.shutdown()


def start_job(comp: Compressor, video: Path, client_id=None, **opts):
    client_id = client_id or str(uuid.uuid4())
    task = DownloadTask(task_id=uuid.uuid4().hex[:8], client_id=client_id, url="https://example.com/v",
                        created_at=datetime.now())
    src, _ = probe(video)
    media = MediaInfo(id="x", title=video.stem, webpage_url="https://example.com/v", extractor="test",
                      duration=int(src.duration))
    opts.setdefault("target_size_mb", 0.3)
    job = comp.admit(task, media, None, CompressionOptions(**opts), client_id, None)
    dest = job.work_dir / video.name
    shutil.copy(video, dest)
    comp.submit(task.task_id, dest)
    return task, job


def wait_for(predicate, timeout=120, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError("condition not reached in time")


def wait_done(task, timeout=180):
    wait_for(lambda: task.status in TERMINAL_STATUSES, timeout)
    return task


def wait_encoding(job):
    wait_for(lambda: job.proc is not None and job.task.status == TaskStatus.COMPRESSING)


def assert_clean(comp: Compressor, job):
    wait_for(lambda: not job.work_dir.exists(), timeout=15)
    assert not comp.jobs
    assert list(comp.work_dir.iterdir()) == []


def test_result_fits_target(comp, sample_video):
    task, job = start_job(comp, sample_video, target_size_mb=0.3)
    wait_done(task)
    assert task.status == TaskStatus.COMPLETED, task.error
    out = comp.cfg.downloads_dir / task.client_id / task.filename
    assert out.exists() and out.stat().st_size <= 300_000
    r = task.compression_result
    assert r.actual_mb <= r.target_mb and not r.passthrough
    assert task.filename == f"{sample_video.stem} [0.3MB].mp4"
    # plan picked a lower rung for ~190 kbps of video and the file really has it
    real, audio_codec = probe(out)
    assert (real.width, real.height) == (r.width, r.height) and r.height < 360
    assert audio_codec == "aac"
    assert_clean(comp, job)
    # the job record carries estimate vs actual for tuning
    rec = comp.history[-1]
    assert rec["outcome"] == "completed" and rec["actual_seconds"] > 0 and rec["estimate_ratio"]
    assert (comp.state_dir / "jobs.jsonl").exists()


def test_no_audio_track(comp, tmp_path):
    video = make_video(tmp_path / "Silent.mp4", seconds=5, audio=False)
    task, job = start_job(comp, video, target_size_mb=0.2)
    wait_done(task)
    assert task.status == TaskStatus.COMPLETED, task.error
    out = comp.cfg.downloads_dir / task.client_id / task.filename
    _, audio_codec = probe(out)
    assert audio_codec is None and out.stat().st_size <= 200_000


def test_trim(comp, sample_video):
    task, job = start_job(comp, sample_video, target_size_mb=0.3, trim_start=2, trim_end=6)
    wait_done(task)
    assert task.status == TaskStatus.COMPLETED, task.error
    real, _ = probe(comp.cfg.downloads_dir / task.client_id / task.filename)
    assert real.duration == pytest.approx(4, abs=0.3)


def test_passthrough_when_source_already_fits(comp, sample_video):
    task, job = start_job(comp, sample_video, target_size_mb=50)
    wait_done(task)
    assert task.status == TaskStatus.COMPLETED
    assert task.compression_result.passthrough
    assert (comp.cfg.downloads_dir / task.client_id / task.filename).stat().st_size == sample_video.stat().st_size
    assert_clean(comp, job)


def test_failure_cleans_up(comp, tmp_path):
    bad = tmp_path / "Broken.mp4"
    bad.write_bytes(os.urandom(200_000))
    task = DownloadTask(task_id="badbad01", client_id=str(uuid.uuid4()), url="https://e/x", created_at=datetime.now())
    media = MediaInfo(id="x", title="x", webpage_url="https://e/x", extractor="t", duration=10)
    job = comp.admit(task, media, None, CompressionOptions(target_size_mb=1), task.client_id, None)
    shutil.copy(bad, job.work_dir / bad.name)
    comp.submit(task.task_id, job.work_dir / bad.name)
    wait_done(task)
    assert task.status == TaskStatus.FAILED and task.error
    assert_clean(comp, job)


def test_cancel_while_encoding_kills_ffmpeg(comp, long_video):
    task, job = start_job(comp, long_video, target_size_mb=2)
    wait_encoding(job)
    proc = job.proc
    assert comp.cancel(task.task_id)
    wait_done(task)
    assert task.status == TaskStatus.CANCELLED
    assert proc.poll() is not None
    assert_clean(comp, job)
    assert not list((comp.cfg.downloads_dir).glob("*/*.mp4"))


def test_cancel_while_queued(tmp_path, long_video, sample_video):
    comp = make_compressor(tmp_path, compression_max_jobs_per_user=5)
    try:
        first, job1 = start_job(comp, long_video, target_size_mb=2)
        wait_encoding(job1)
        second, job2 = start_job(comp, sample_video)
        assert second.status == TaskStatus.QUEUED and second.queue_position == 1
        assert comp.cancel(second.task_id)
        assert second.status == TaskStatus.CANCELLED
        assert not job2.work_dir.exists()
        comp.cancel(first.task_id)
        wait_done(first)
    finally:
        comp.shutdown()


def test_timeout_kills_and_cleans(tmp_path, long_video):
    # Absurdly optimistic throughput -> tiny estimate -> 1 s timeout
    comp = make_compressor(tmp_path, compression_throughput_override=1e13,
                           compression_min_timeout_sec=1, compression_timeout_multiplier=1)
    try:
        task, job = start_job(comp, long_video, target_size_mb=2)
        wait_done(task)
        assert task.status == TaskStatus.FAILED and "timed out" in task.error
        assert_clean(comp, job)
    finally:
        comp.shutdown()


def test_ffmpeg_crash_does_not_kill_worker(comp, long_video, sample_video):
    task, job = start_job(comp, long_video, target_size_mb=2)
    wait_encoding(job)
    os.kill(job.proc.pid, signal.SIGKILL)  # what the OOM killer would do
    wait_done(task)
    assert task.status == TaskStatus.FAILED and "killed" in task.error
    assert_clean(comp, job)
    # the same worker keeps serving jobs
    task2, _ = start_job(comp, sample_video)
    wait_done(task2)
    assert task2.status == TaskStatus.COMPLETED


def test_orphans_removed_on_start(tmp_path):
    work = tmp_path / "downloads" / ".work"
    (work / "deadbeef").mkdir(parents=True)
    (work / "deadbeef" / "input.mp4").write_bytes(b"x")
    (work / "stray.tmp").write_bytes(b"x")
    comp = make_compressor(tmp_path)
    try:
        assert list(work.iterdir()) == []
    finally:
        comp.shutdown()


def test_concurrency_limit_holds(tmp_path, sample_video):
    comp = make_compressor(tmp_path, compression_max_concurrent_jobs=1,
                           compression_max_jobs_per_user=10, compression_max_queue_length=10)
    try:
        jobs = [start_job(comp, sample_video, client_id="same-user") for _ in range(4)]
        tasks = [t for t, _ in jobs]
        peak = 0
        while not all(t.status in TERMINAL_STATUSES for t in tasks):
            running = sum(t.status == TaskStatus.COMPRESSING for t in tasks)
            queued = [t for t in tasks if t.status == TaskStatus.QUEUED]
            peak = max(peak, running)
            assert running <= 1
            assert sorted(t.queue_position for t in queued) == list(range(1, len(queued) + 1))
            time.sleep(0.02)
        assert all(t.status == TaskStatus.COMPLETED for t in tasks)
        assert comp.max_running_seen == 1 and peak == 1
        assert len({t.filename for t in tasks}) == 4  # unique names, nothing overwritten
    finally:
        comp.shutdown()


def test_two_workers_run_in_parallel(tmp_path, long_video):
    comp = make_compressor(tmp_path, compression_cpu_cores=2, compression_max_jobs_per_user=10)
    try:
        tasks = [start_job(comp, long_video, client_id="u", target_size_mb=2)[0] for _ in range(3)]
        wait_for(lambda: comp.max_running_seen == 2)
        assert comp.running_now <= 2
        for t in tasks:
            comp.cancel(t.task_id)
            wait_done(t)
    finally:
        comp.shutdown()


def test_release_before_queue_cleans_reservation(comp, sample_video):
    task = DownloadTask(task_id="rel00001", client_id=str(uuid.uuid4()), url="https://e/x", created_at=datetime.now())
    media = MediaInfo(id="x", title="x", webpage_url="https://e/x", extractor="t", duration=10)
    job = comp.admit(task, media, None, CompressionOptions(target_size_mb=1), task.client_id, None)
    (job.work_dir / "partial.mp4.part").write_bytes(b"x")
    comp.release(task.task_id)
    assert not job.work_dir.exists() and not comp.jobs


def test_ffmpeg_runs_sandboxed(comp, long_video):
    task, job = start_job(comp, long_video, target_size_mb=2)
    wait_encoding(job)
    pid = job.proc.pid
    try:
        proc_dir = Path(f"/proc/{pid}")
        # the wrapper chain exec()s, so the child pid is ffmpeg itself
        assert b"ffmpeg" in (proc_dir / "cmdline").read_bytes().split(b"\0")[0]
        assert (proc_dir / "oom_score_adj").read_text().strip() == "1000"
        nice = int((proc_dir / "stat").read_text().rsplit(")", 1)[1].split()[16])
        assert nice == 19
        limits = (proc_dir / "limits").read_text()
        line = next(l for l in limits.splitlines() if l.startswith("Max address space"))
        assert line.split()[3] != "unlimited"
        assert os.getpgid(pid) == pid  # own process group, killed as a whole
        if shutil.which("ionice"):
            import subprocess
            out = subprocess.run(["ionice", "-p", str(pid)], capture_output=True, text=True).stdout
            assert "idle" in out
    finally:
        comp.cancel(task.task_id)
        wait_done(task)
