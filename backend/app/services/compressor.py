"""Target-size video compression: admission, queue, sandboxed ffmpeg workers.

Lifecycle of a compression task:
  admit()  - estimate from yt-dlp metadata before anything is downloaded; on
             success the job reserves its slot, time and disk in the queue model
  (download into downloads/.work/<task_id>/ by TaskManager)
  submit() - the downloaded input joins the worker queue (QUEUED)
  worker   - ffprobe, re-plan with real metadata, two-pass libx264 with
             nice/ionice/address-space limit/oom_score_adj, size check and
             pass-2 retries, move the result into the user directory
Every exit path removes the job's work directory; leftovers from a crash or
restart are removed at startup.
"""

import itertools
import json
import logging
import os
import shutil
import signal
import statistics
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

from ..config import settings
from ..models import (
    CompressionOptions, CompressionResult, DownloadTask, MediaInfo, TaskStatus,
)
from .trimmer import clip_label
from .compression_calibrator import ThroughputModel, read_cpu_times, steal_between
from .compression_planner import (
    MB, EncodePlan, Evaluation, PlanError, PlannerConfig, QueueSnapshot, SourceVideo,
    TargetSpec, build_alternatives, estimate_source_bytes, evaluate, format_duration,
    has_h264_alternative,
    normalize_codec, pass2_work_units, plan_encode,
)

logger = logging.getLogger(__name__)

KILL_GRACE_SECONDS = 5
JOB_LOG_MAX_BYTES = 5 * 1024 * 1024
RETRY_MARGIN = 0.98
MONO_AUDIO_KBPS = 48  # at or below this, encode mono


class JobCancelled(Exception):
    pass


class EncodeError(Exception):
    pass


class AdmissionRejected(Exception):
    def __init__(self, detail: dict):
        super().__init__(detail.get("message", "Compression request rejected"))
        self.detail = detail


@dataclass
class CompressionJob:
    task: DownloadTask
    client_id: str
    client_ip: Optional[str]
    options: CompressionOptions
    target: TargetSpec
    label: str  # file name suffix: preset id or "<N>MB"
    plan: EncodePlan
    source: SourceVideo
    deferred: bool
    work_dir: Path
    seq: int
    admitted_at: float = field(default_factory=time.monotonic)
    state: str = "downloading"  # downloading -> queued -> running
    input_path: Optional[Path] = None
    progress: float = 0.0  # 0..1 over both passes
    cancel: threading.Event = field(default_factory=threading.Event)
    proc: Optional[subprocess.Popen] = None
    timed_out: bool = False
    record: dict = field(default_factory=dict)

    @property
    def reserved_seconds(self) -> float:
        return self.plan.est_seconds * (1 - self.progress)

    @property
    def reserved_disk(self) -> int:
        # Until the input is on disk, reserve the estimate; afterwards it is already counted as used
        if self.state != "downloading":
            return 0
        return self.record.get("est_input_bytes", 0)


# ---------------------------------------------------------------- system probes

def _read_int(path: str) -> Optional[int]:
    try:
        raw = Path(path).read_text().strip()
        return None if raw == "max" else int(raw)
    except (OSError, ValueError):
        return None


def _meminfo() -> Dict[str, int]:
    out = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            out[key] = int(rest.split()[0]) // 1024  # MB
    except (OSError, ValueError, IndexError):
        pass
    return out


def ram_capacity_mb() -> int:
    total = _meminfo().get("MemTotal", 0) or 10**6
    limit = _read_int("/sys/fs/cgroup/memory.max") or _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit and limit < 1 << 60:
        total = min(total, limit // (1024 * 1024))
    return total


def ram_available_mb() -> int:
    avail = _meminfo().get("MemAvailable", 0) or 10**6
    limit = _read_int("/sys/fs/cgroup/memory.max")
    current = _read_int("/sys/fs/cgroup/memory.current")
    if limit and current is not None:
        inactive = 0
        try:
            for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines():
                if line.startswith("inactive_file "):
                    inactive = int(line.split()[1])
        except (OSError, ValueError):
            pass
        avail = min(avail, (limit - current + inactive) // (1024 * 1024))
    return avail


def _sandbox_prefix(ram_limit_mb: int) -> List[str]:
    """Lowest CPU/IO priority, address-space cap, and first in line for the OOM killer."""
    prefix: List[str] = []
    if shutil.which("prlimit"):
        prefix += ["prlimit", f"--as={ram_limit_mb * 1024 * 1024}", "--"]
    if shutil.which("nice"):
        prefix += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3", "-t"]
    # Raising our own oom_score_adj needs no privileges
    prefix += ["sh", "-c", 'echo 1000 > /proc/self/oom_score_adj 2>/dev/null; exec "$@"', "ffmpeg-sandbox"]
    return prefix


def address_space_limit_mb(ram_mb: int) -> int:
    # RLIMIT_AS caps virtual memory, which for ffmpeg (shared libs, thread stacks,
    # allocator arenas) is far above RSS. This catches runaways; the container
    # mem_limit plus oom_score_adj is what protects the web process.
    return max(ram_mb * 4, 2048)


def kill_tree(proc: subprocess.Popen) -> None:
    """SIGTERM the process group, SIGKILL after a grace period."""
    if proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def probe(path: Path) -> Tuple[SourceVideo, Optional[str]]:
    """Real source parameters and audio codec name via ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=60, check=True,
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        raise EncodeError(f"Could not read the downloaded video: {e}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if not video or not video.get("width") or not video.get("height"):
        raise EncodeError("The downloaded file has no video stream.")
    w, h = int(video["width"]), int(video["height"])
    rotation = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    rotation = rotation or int(float((video.get("tags") or {}).get("rotate", 0) or 0))
    if abs(rotation) % 180 == 90:
        w, h = h, w  # ffmpeg autorotates, so the output aspect follows the display orientation
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = (video.get(key) or "0/0").partition("/")
        try:
            if float(den or 1) > 0 and float(num) > 0:
                fps = float(num) / float(den or 1)
                break
        except ValueError:
            continue
    duration = None
    for raw in ((data.get("format") or {}).get("duration"), video.get("duration")):
        try:
            if raw is not None and float(raw) > 0:
                duration = float(raw)
                break
        except ValueError:
            continue
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return SourceVideo(
        duration=duration, width=w, height=h, fps=fps or 30.0,
        vcodec=normalize_codec(video.get("codec_name")),
        has_audio=audio is not None,
        size_bytes=path.stat().st_size,
    ), (audio or {}).get("codec_name")


# ---------------------------------------------------------------- compressor

class Compressor:
    def __init__(self, cfg=settings, model: Optional[ThroughputModel] = None):
        self.cfg = cfg
        self.pcfg = PlannerConfig.from_settings(cfg)
        self.work_dir = cfg.compression_work_dir
        self.state_dir = cfg.compression_state_dir
        self.model = model or ThroughputModel(cfg=cfg)
        self.presets = self._load_presets()
        self.jobs: Dict[str, CompressionJob] = {}
        self._cond = threading.Condition()
        self._seq = itertools.count()
        self._workers: List[threading.Thread] = []
        self._running = False
        self.running_now = 0
        self.max_running_seen = 0
        self.history: Deque[dict] = deque(maxlen=200)
        self.admissions: Counter = Counter()

    # ------------------------------------------------------------ lifecycle

    def start(self, calibrate: bool = True) -> None:
        self.cleanup_orphans()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self.ensure_workers()
        if calibrate and self.cfg.compression_enabled:
            threading.Thread(target=self.model.calibrate, name="compression-calibrate", daemon=True).start()
        logger.info(f"Compression ready: {self.pcfg.concurrency} concurrent job(s), "
                    f"{self.cfg.compression_ffmpeg_threads} thread(s) each")

    def shutdown(self) -> None:
        with self._cond:
            self._running = False
            jobs = list(self.jobs.values())
            self._cond.notify_all()
        for job in jobs:
            job.cancel.set()
            if job.proc:
                kill_tree(job.proc)
        for t in self._workers:
            t.join(timeout=KILL_GRACE_SECONDS * 2 + 1)
        for job in jobs:
            shutil.rmtree(job.work_dir, ignore_errors=True)

    def ensure_workers(self) -> None:
        """Start missing worker threads (also revives a worker that died unexpectedly)."""
        if not self._running:
            return
        self._workers = [t for t in self._workers if t.is_alive()]
        while len(self._workers) < self.pcfg.concurrency:
            t = threading.Thread(target=self._worker_loop, name=f"compression-worker-{len(self._workers)}",
                                 daemon=True)
            t.start()
            self._workers.append(t)

    def cleanup_orphans(self) -> None:
        """Tasks don't survive a restart, so anything left in the work dir is garbage."""
        if self.work_dir.exists():
            for entry in self.work_dir.iterdir():
                if entry.name in {job.work_dir.name for job in self.jobs.values()}:
                    continue
                logger.info(f"Removing orphaned compression work dir: {entry.name}")
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def tick(self) -> None:
        """Periodic housekeeping (scheduler): steal sample, heartbeat, worker watchdog."""
        self.model.steal.sample()
        with self._cond:
            jobs = list(self.jobs.values())
        for job in jobs:
            # md-autoupdate treats recently touched files under .work as activity
            try:
                (job.work_dir / ".heartbeat").touch()
            except OSError:
                pass
        self.ensure_workers()

    # ------------------------------------------------------------ presets

    def _load_presets(self) -> List[dict]:
        try:
            data = json.loads(Path(self.cfg.compression_presets_file).read_text())
        except (OSError, ValueError) as e:
            logger.error(f"Could not load compression presets: {e}")
            return []
        presets = []
        for p in data:
            if not p.get("id") or not p.get("name") or not p.get("target_size_mb"):
                logger.warning(f"Skipping invalid compression preset: {p}")
                continue
            presets.append(p)
        return presets

    def public_presets(self) -> List[dict]:
        return [
            {k: p.get(k) for k in ("id", "name", "target_size_mb", "max_resolution", "audio_kbps")}
            for p in self.presets if not p.get("hidden")
        ]

    def resolve_target(self, opts: CompressionOptions) -> Tuple[TargetSpec, str]:
        max_height, audio_cap = opts.max_height, None
        if opts.preset_id:
            preset = next((p for p in self.presets if p["id"] == opts.preset_id), None)
            if not preset:
                raise PlanError("invalid_preset", f"Unknown compression preset: {opts.preset_id}")
            target_mb = float(preset["target_size_mb"])
            if preset.get("max_resolution"):
                max_height = min(filter(None, (max_height, int(preset["max_resolution"]))))
            audio_cap = preset.get("audio_kbps")
            label = preset["id"]
        elif opts.target_size_mb:
            target_mb = opts.target_size_mb
            label = f"{target_mb:g}MB"
        else:
            raise PlanError("missing_target", "Choose a preset or enter a target size in MB.")
        return TargetSpec(
            target_bytes=int(target_mb * MB), max_height=max_height, resolution=opts.resolution,
            audio_kbps_cap=audio_cap,
            encoder_preset=opts.encoder_preset, trim_start=opts.trim_start, trim_end=opts.trim_end,
        ), label

    # ------------------------------------------------------------ admission

    def _snapshot(self, client_id: Optional[str], client_ip: Optional[str]) -> QueueSnapshot:
        """Caller holds self._cond."""
        jobs = list(self.jobs.values())
        try:
            free = shutil.disk_usage(self.cfg.downloads_dir).free
        except OSError:
            free = 0
        return QueueSnapshot(
            remaining_seconds=[j.reserved_seconds for j in jobs],
            waiting_count=sum(1 for j in jobs if j.state != "running"),
            user_jobs=sum(1 for j in jobs if client_id and j.client_id == client_id),
            ip_jobs=sum(1 for j in jobs if client_ip and j.client_ip == client_ip),
            free_disk_bytes=free,
            reserved_disk_bytes=sum(j.reserved_disk for j in jobs),
            ram_capacity_mb=ram_capacity_mb(),
        )

    def _evaluate(self, media: MediaInfo, quality_cap: Optional[int], opts: CompressionOptions,
                  snap: QueueSnapshot) -> Evaluation:
        try:
            target, _ = self.resolve_target(opts)
        except PlanError as e:
            return Evaluation(ok=False, code=e.code, message=e.message)
        return evaluate(media.formats, media.duration, quality_cap, target, opts.prefer_h264,
                        opts.deferred, snap, self.model.perf(), self.pcfg)

    def estimate(self, media: MediaInfo, quality_cap: Optional[int], opts: CompressionOptions,
                 client_id: Optional[str], client_ip: Optional[str]) -> dict:
        """Dry run: admission decision plus alternatives, nothing reserved."""
        if not self.cfg.compression_enabled:
            return {"admitted": False, "code": "disabled", "message": "Compression is disabled on this server.",
                    "alternatives": []}
        with self._cond:
            snap = self._snapshot(client_id, client_ip)
            ev = self._evaluate(media, quality_cap, opts, snap)
            return self._response(ev, media, quality_cap, opts, snap)

    def _response(self, ev: Evaluation, media: MediaInfo, quality_cap: Optional[int],
                  opts: CompressionOptions, snap: QueueSnapshot) -> dict:
        body = ev.summary()
        try:
            body["target_mb"] = self.resolve_target(opts)[0].target_bytes / MB
        except PlanError:
            body["target_mb"] = None
        body["alternatives"] = [] if ev.ok else self._alternatives(ev, media, quality_cap, opts, snap)
        return body

    def _alternatives(self, ev: Evaluation, media: MediaInfo, quality_cap: Optional[int],
                      opts: CompressionOptions, snap: QueueSnapshot) -> List[dict]:
        def run(d: dict) -> Evaluation:
            return self._evaluate(media, quality_cap, CompressionOptions(**d), snap)

        cap = min(c for c in (quality_cap, opts.max_height, opts.resolution, self.pcfg.max_input_height) if c)
        h264 = bool(ev.source) and has_h264_alternative(media.formats, cap, ev.source)
        return build_alternatives(opts.model_dump(), ev, run, self.pcfg, media.duration, h264)

    def admit(self, task: DownloadTask, media: MediaInfo, quality_cap: Optional[int],
              opts: CompressionOptions, client_id: str, client_ip: Optional[str]) -> CompressionJob:
        """Atomically check admission and reserve; raises AdmissionRejected."""
        if not self.cfg.compression_enabled:
            raise AdmissionRejected({"code": "disabled", "message": "Compression is disabled on this server.",
                                     "alternatives": []})
        with self._cond:
            snap = self._snapshot(client_id, client_ip)
            ev = self._evaluate(media, quality_cap, opts, snap)
            log = {
                "task_id": task.task_id, "admitted": ev.ok, "code": ev.code,
                "estimate": ev.summary(), "queue_jobs": len(self.jobs),
                "perf": self.model.snapshot(),
            }
            logger.info(f"compression_admission {json.dumps(log, default=str)}")
            self.admissions[ev.code] += 1
            if not ev.ok:
                raise AdmissionRejected(self._response(ev, media, quality_cap, opts, snap))

            target, label = self.resolve_target(opts)
            work_dir = self.work_dir / task.task_id
            work_dir.mkdir(parents=True, exist_ok=True)
            job = CompressionJob(
                task=task, client_id=client_id, client_ip=client_ip, options=opts,
                target=target, label=label, plan=ev.plan, source=ev.source,
                deferred=opts.deferred, work_dir=work_dir, seq=next(self._seq),
            )
            job.record = {
                "task_id": task.task_id,
                "admission": ev.summary(),
                "est_input_bytes": estimate_source_bytes(ev.source, ev.source.duration or ev.plan.duration),
                "target_bytes": target.target_bytes,
            }
            self.jobs[task.task_id] = job
            task.compression_target_mb = target.target_bytes / MB
            task.estimate_seconds = round(ev.plan.est_seconds)
            return job

    # ------------------------------------------------------------ queue

    def get(self, task_id: str) -> Optional[CompressionJob]:
        with self._cond:
            return self.jobs.get(task_id)

    def submit(self, task_id: str, input_path: Path) -> None:
        with self._cond:
            job = self.jobs.get(task_id)
            if not job:
                return
            job.input_path = input_path
            job.state = "queued"
            job.task.status = TaskStatus.QUEUED
            job.task.progress = 0
            job.task.speed = job.task.eta = None
            self._update_positions()
            self._cond.notify_all()

    def release(self, task_id: str) -> None:
        """Drop a job that never reached the worker (download failed or cancelled)."""
        with self._cond:
            job = self.jobs.pop(task_id, None)
            self._update_positions()
            self._cond.notify_all()
        if job:
            shutil.rmtree(job.work_dir, ignore_errors=True)

    def cancel(self, task_id: str) -> bool:
        with self._cond:
            job = self.jobs.get(task_id)
            if not job:
                return False
            job.cancel.set()
            if job.state == "queued":
                self.jobs.pop(task_id, None)
                job.task.status = TaskStatus.CANCELLED
                job.task.queue_position = None
                job.task.completed_at = datetime.now()
                self._update_positions()
                self._cond.notify_all()
                cleanup = True
            else:
                cleanup = False  # the download or worker thread cleans up on its own
            proc = job.proc
        if proc:
            kill_tree(proc)
        if cleanup:
            shutil.rmtree(job.work_dir, ignore_errors=True)
            self._finish_record(job, "cancelled")
        return True

    def _waiting(self) -> List[CompressionJob]:
        return sorted((j for j in self.jobs.values() if j.state == "queued"),
                      key=lambda j: (j.deferred, j.seq))

    def _update_positions(self) -> None:
        for i, job in enumerate(self._waiting(), 1):
            job.task.queue_position = i

    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._waiting():
                    self._cond.wait(timeout=5)
                if not self._running:
                    return
                job = self._waiting()[0]
                job.state = "running"
                job.task.queue_position = None
                self.running_now += 1
                self.max_running_seen = max(self.max_running_seen, self.running_now)
                self._update_positions()
            try:
                self._run_job(job)
            except Exception as e:  # never let a job kill the worker
                logger.exception(f"Compression worker error for {job.task.task_id}: {e}")
                self._fail(job, "Internal error during compression.")
            finally:
                if job.proc:
                    kill_tree(job.proc)
                shutil.rmtree(job.work_dir, ignore_errors=True)
                with self._cond:
                    self.jobs.pop(job.task.task_id, None)
                    self.running_now -= 1
                    self._update_positions()
                    self._cond.notify_all()

    # ------------------------------------------------------------ encoding

    def _fail(self, job: CompressionJob, message: str) -> None:
        job.task.status = TaskStatus.FAILED
        job.task.error = message
        job.task.compression_pass = None
        job.task.completed_at = datetime.now()
        self._finish_record(job, "failed", error=message)

    def _run_job(self, job: CompressionJob) -> None:
        task = job.task
        task.status = TaskStatus.COMPRESSING
        task.progress = 0
        rec = job.record
        rec["queue_wait_seconds"] = round(time.monotonic() - job.admitted_at)
        try:
            if job.cancel.is_set():
                raise JobCancelled()
            src, audio_codec = probe(job.input_path)
            plan = self._replan(job, src)
            rec.update({"source": {"width": src.width, "height": src.height, "fps": round(src.fps, 3),
                                   "vcodec": src.vcodec, "bytes": src.size_bytes},
                        "plan": _plan_dict(plan)})

            if self._passthrough(job, src, audio_codec):
                return
            self._wait_for_resources(job, plan)
            deadline = time.monotonic() + max(self.cfg.compression_min_timeout_sec,
                                              plan.est_seconds * self.cfg.compression_timeout_multiplier)
            self._encode(job, src, plan, deadline)
        except JobCancelled:
            task.status = TaskStatus.CANCELLED
            task.compression_pass = None
            task.completed_at = datetime.now()
            self._finish_record(job, "cancelled")
        except (PlanError, EncodeError) as e:
            self._fail(job, getattr(e, "message", None) or str(e))

    def _replan(self, job: CompressionJob, src: SourceVideo) -> EncodePlan:
        """Plan again with the real file; it must still fit the time budget."""
        if src.short_side > self.pcfg.max_input_height:
            raise PlanError("source_too_large",
                            f"The downloaded video is {src.width}x{src.height}, above the "
                            f"{self.pcfg.max_input_height}p limit for compression.")
        perf = self.model.perf()
        plan = plan_encode(src, job.target, job.plan.encoder_preset, perf, self.pcfg)
        sf = self.pcfg.safety_factor
        budget = self.pcfg.max_total_wait
        if not job.deferred:
            budget -= time.monotonic() - job.admitted_at
        # The quality upgrade is opportunistic: drop it if it no longer fits
        if (plan.encoder_preset != self.pcfg.base_preset and not job.target.encoder_preset
                and plan.est_seconds * sf > budget):
            plan = plan_encode(src, job.target, self.pcfg.base_preset, perf, self.pcfg)
        if plan.est_seconds * sf > self.pcfg.max_total_wait:
            raise PlanError("too_slow",
                            f"The downloaded video turned out heavier than its metadata suggested: "
                            f"encoding would take about {format_duration(plan.est_seconds * sf)}.")
        with self._cond:
            job.plan = plan
            job.source = src
        job.task.estimate_seconds = round(plan.est_seconds)
        return plan

    def _passthrough(self, job: CompressionJob, src: SourceVideo, audio_codec: Optional[str]) -> bool:
        """The source already fits and plays everywhere: keep it as is."""
        if (job.target.trim_start or job.target.trim_end
                or src.size_bytes > job.target.target_bytes
                or job.input_path.suffix.lower() != ".mp4"
                or src.vcodec != "h264" or audio_codec not in (None, "aac")
                or (job.target.max_height and src.short_side > job.target.max_height)):
            return False
        self._deliver(job, job.input_path, src.width, src.height, src.fps, retries=0, passthrough=True)
        return True

    def _wait_for_resources(self, job: CompressionJob, plan: EncodePlan) -> None:
        need_ram = plan.ram_mb + self.pcfg.min_free_ram_mb
        deadline = time.monotonic() + self.cfg.compression_ram_wait_sec
        while ram_available_mb() < need_ram:
            if time.monotonic() > deadline:
                raise EncodeError("Not enough free memory on the server to encode right now.")
            if job.cancel.wait(5):
                raise JobCancelled()
        free = shutil.disk_usage(self.cfg.downloads_dir).free
        if free < plan.target_bytes * 1.5 + self.pcfg.min_free_disk_bytes:
            raise EncodeError("The server is low on disk space right now.")

    def _encode(self, job: CompressionJob, src: SourceVideo, plan: EncodePlan, deadline: float) -> None:
        task, rec = job.task, job.record
        out = job.work_dir / "output.mp4"
        weight1 = 1 - 1 / self.model.two_pass_factor()  # share of pass 1 in the total time
        before = read_cpu_times()

        t1 = self._ffmpeg(job, self._args(job, src, plan, 1, plan.video_kbps, out), plan.duration,
                          deadline, pass_no=1, offset=0.0, weight=weight1)
        mid = read_cpu_times()
        video_kbps = plan.video_kbps
        retries = 0
        t2 = self._ffmpeg(job, self._args(job, src, plan, 2, video_kbps, out), plan.duration,
                          deadline, pass_no=2, offset=weight1, weight=1 - weight1)
        steal2 = steal_between(mid, read_cpu_times())
        size = out.stat().st_size
        while size > plan.target_bytes:
            if retries >= self.cfg.compression_max_retries:
                raise EncodeError(
                    f"Could not get the file under {plan.target_bytes / MB:g} MB "
                    f"(last attempt {size / MB:.2f} MB after {retries + 1} tries).")
            retries += 1
            # Attribute the overshoot to video; the pass-1 stats stay valid for a new bitrate
            excess_kbps = (size - plan.target_bytes) * 8 / plan.duration / 1000
            video_kbps = int((video_kbps - excess_kbps) * RETRY_MARGIN)
            if video_kbps <= 0:
                raise EncodeError("The target size is too small for this video.")
            logger.info(f"Task {task.task_id}: {size / MB:.2f} MB over target, "
                        f"retrying pass 2 at {video_kbps} kbps")
            self._ffmpeg(job, self._args(job, src, plan, 2, video_kbps, out), plan.duration,
                         deadline, pass_no=2, offset=weight1, weight=1 - weight1)
            size = out.stat().st_size

        work = pass2_work_units(plan.duration, src, plan.width, plan.height, plan.fps,
                                plan.encoder_preset, self.pcfg)
        self.model.observe(work, t1, t2, steal2)
        rec.update({
            "pass1_seconds": round(t1, 1), "pass2_seconds": round(t2, 1),
            "actual_seconds": round(t1 + t2, 1), "steal": round(steal_between(before, read_cpu_times()), 3),
            "estimate_ratio": round((t1 + t2) / plan.est_seconds, 3) if plan.est_seconds else None,
            "final_video_kbps": video_kbps,
        })
        self._deliver(job, out, plan.width, plan.height, plan.fps, retries=retries, passthrough=False)

    def _args(self, job: CompressionJob, src: SourceVideo, plan: EncodePlan, pass_no: int,
              video_kbps: int, out: Path) -> List[str]:
        threads = str(self.cfg.compression_ffmpeg_threads)
        args = ["-threads", threads]
        if job.target.trim_start:
            args += ["-ss", f"{job.target.trim_start:.3f}"]
        args += ["-i", str(job.input_path)]
        if job.target.trim_start or job.target.trim_end:
            args += ["-t", f"{plan.duration:.3f}"]
        filters = []
        if (plan.width, plan.height) != (src.width, src.height):
            filters.append(f"scale={plan.width}:{plan.height}")
        if plan.fps < src.fps - 0.01:
            filters.append(f"fps={plan.fps:g}")
        args += ["-map", "0:v:0"]
        if filters:
            args += ["-vf", ",".join(filters), "-filter_threads", threads]
        args += [
            "-c:v", self.cfg.compression_encoder, "-preset", plan.encoder_preset,
            "-b:v", f"{video_kbps}k", "-pass", str(pass_no),
            "-passlogfile", str(job.work_dir / "x264"), "-threads", threads, "-pix_fmt", "yuv420p",
        ]
        if pass_no == 1:
            return args + ["-an", "-f", "null", os.devnull]
        if plan.audio_kbps:
            channels = "1" if plan.audio_kbps <= MONO_AUDIO_KBPS else "2"
            args += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{plan.audio_kbps}k", "-ac", channels]
        else:
            args += ["-an"]
        return args + ["-sn", "-dn", "-movflags", "+faststart", str(out)]

    def _ffmpeg(self, job: CompressionJob, args: List[str], duration: float, deadline: float,
                pass_no: int, offset: float, weight: float) -> float:
        """Run one sandboxed ffmpeg pass; returns its wall time."""
        if job.cancel.is_set():
            raise JobCancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EncodeError("Encoding timed out.")
        task = job.task
        task.compression_pass = pass_no
        task.progress = 0
        cmd = _sandbox_prefix(address_space_limit_mb(job.plan.ram_mb)) + [
            "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error",
            "-progress", "pipe:1", "-y", *args,
        ]
        log_path = job.work_dir / f"ffmpeg-pass{pass_no}.log"
        start = time.monotonic()
        with open(log_path, "wb") as stderr:
            with self._cond:
                if job.cancel.is_set():
                    raise JobCancelled()
                job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr,
                                            stdin=subprocess.DEVNULL, cwd=job.work_dir,
                                            start_new_session=True)
            proc = job.proc

            def on_timeout():
                job.timed_out = True
                kill_tree(proc)

            timer = threading.Timer(remaining, on_timeout)
            timer.daemon = True
            timer.start()
            try:
                for raw in proc.stdout:
                    key, _, value = raw.decode(errors="replace").strip().partition("=")
                    if key in ("out_time_us", "out_time_ms") and value.isdigit() and duration:
                        frac = min(1.0, int(value) / 1e6 / duration)
                        task.progress = round(frac * 100, 1)
                        job.progress = offset + weight * frac
                rc = proc.wait()
            finally:
                timer.cancel()
                proc.stdout.close()
                job.proc = None
        elapsed = time.monotonic() - start
        if job.cancel.is_set():
            raise JobCancelled()
        if job.timed_out:
            raise EncodeError(f"Encoding timed out after {format_duration(time.monotonic() - job.admitted_at)}.")
        if rc != 0:
            tail = _tail(log_path)
            logger.warning(f"Task {task.task_id} ffmpeg pass {pass_no} exited with {rc}: {tail}")
            if rc in (-signal.SIGKILL, 128 + signal.SIGKILL, 137):
                raise EncodeError("The encoder was killed (out of memory?).")
            raise EncodeError(f"The encoder failed (pass {pass_no}).")
        return elapsed

    def _deliver(self, job: CompressionJob, path: Path, width: int, height: int, fps: float,
                 retries: int, passthrough: bool) -> None:
        """Move the result (and thumbnail) into the user directory; the original is not kept."""
        user_dir = self.cfg.downloads_dir / job.client_id
        user_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{job.input_path.stem} [{job.label}]"
        if job.target.trim_start or job.target.trim_end is not None:
            stem += f" [{clip_label(job.target.trim_start, job.target.trim_end)}]"
        dest = user_dir / f"{stem}.mp4"
        n = 2
        while dest.exists():
            dest = user_dir / f"{stem} ({n}).mp4"
            n += 1
        size = path.stat().st_size
        os.replace(path, dest)
        for thumb in job.work_dir.iterdir():
            if thumb.stem == job.input_path.stem and thumb.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                os.replace(thumb, dest.with_suffix(thumb.suffix.lower()))
                break

        task = job.task
        task.compression_result = CompressionResult(
            target_mb=round(job.target.target_bytes / MB, 2), actual_mb=round(size / MB, 2),
            width=width, height=height, fps=round(fps, 2),
            encoder_preset="copy" if passthrough else job.plan.encoder_preset,
            retries=retries, passthrough=passthrough,
        )
        task.filename = dest.name
        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.compression_pass = None
        task.completed_at = datetime.now()
        job.record.update({"actual_bytes": size, "retries": retries, "passthrough": passthrough,
                           "output": {"width": width, "height": height, "fps": round(fps, 3)}})
        self._finish_record(job, "completed")
        logger.info(f"Task {task.task_id} compressed: {dest.name} ({size / MB:.2f} MB)")

    # ------------------------------------------------------------ observability

    def _finish_record(self, job: CompressionJob, outcome: str, **extra) -> None:
        rec = dict(job.record, outcome=outcome, finished_at=datetime.now().isoformat(), **extra)
        rec["total_seconds"] = round(time.monotonic() - job.admitted_at, 1)
        self.history.append(rec)
        line = json.dumps(rec, default=str)
        logger.info(f"compression_job {line}")
        try:
            path = self.state_dir / "jobs.jsonl"
            self.state_dir.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > JOB_LOG_MAX_BYTES:
                os.replace(path, path.with_suffix(".jsonl.1"))
            with open(path, "a") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.warning(f"Could not write compression job log: {e}")

    def stats(self) -> dict:
        done = [r for r in self.history if r.get("outcome") == "completed" and not r.get("passthrough")]
        by_codec: Dict[str, List[float]] = {}
        for r in done:
            if r.get("estimate_ratio"):
                by_codec.setdefault(r.get("source", {}).get("vcodec", "unknown"), []).append(r["estimate_ratio"])

        def dist(values):
            if not values:
                return None
            values = sorted(values)
            return {"n": len(values), "median": round(statistics.median(values), 3),
                    "p90": round(values[min(len(values) - 1, int(len(values) * 0.9))], 3),
                    "max": round(values[-1], 3)}

        with self._cond:
            jobs = list(self.jobs.values())
        outcomes = Counter(r.get("outcome") for r in self.history)
        return {
            "enabled": self.cfg.compression_enabled,
            "perf": self.model.snapshot(),
            "queue": {
                "running": sum(1 for j in jobs if j.state == "running"),
                "queued": sum(1 for j in jobs if j.state == "queued"),
                "downloading": sum(1 for j in jobs if j.state == "downloading"),
                "concurrency": self.pcfg.concurrency,
            },
            # actual / estimated encode time; > 1 means estimates are optimistic
            "estimate_ratio": dist([v for vs in by_codec.values() for v in vs]),
            "estimate_ratio_by_codec": {k: dist(v) for k, v in by_codec.items()},
            "size_ratio": dist([r["actual_bytes"] / r["target_bytes"] for r in done
                                if r.get("actual_bytes") and r.get("target_bytes")]),
            "retries": sum(r.get("retries", 0) for r in done),
            "outcomes": dict(outcomes),
            "admissions": dict(self.admissions),
        }


def _plan_dict(plan: EncodePlan) -> dict:
    return {"width": plan.width, "height": plan.height, "fps": plan.fps, "video_kbps": plan.video_kbps,
            "audio_kbps": plan.audio_kbps, "bpp": plan.bpp, "preset": plan.encoder_preset,
            "duration": plan.duration, "est_seconds": round(plan.est_seconds, 1), "ram_mb": plan.ram_mb}


def _tail(path: Path, limit: int = 600) -> str:
    try:
        return path.read_bytes()[-limit:].decode(errors="replace").strip()
    except OSError:
        return ""


def quality_cap(quality: Optional[str]) -> Optional[int]:
    """'1080p' -> 1080, '4k' -> 2160, 'best'/None -> None."""
    if not quality or quality in ("best", "audio_only"):
        return None
    if quality == "4k":
        return 2160
    digits = "".join(ch for ch in quality if ch.isdigit())
    return int(digits) if digits else None


# Singleton instance
compressor = Compressor()
