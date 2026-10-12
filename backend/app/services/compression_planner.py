"""Pure planning logic for target-size video compression.

No I/O here: bitrate budget, resolution ladder, source format choice, cost and
RAM estimates, admission decision and the list of alternatives offered when a
request is rejected. Everything is driven by PlannerConfig and plain data, so it
is unit-tested in isolation (see backend/tests/test_compression_planner.py).
"""

import math
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence

MB = 1_000_000  # decimal megabytes: the stricter reading of platform limits

# RAM model for one libx264 job (MB): fixed overhead plus frame buffers.
# x264 keeps ~lookahead + refs + threading frames of the output, the decoder a
# handful of input frames; 1.5 bytes per pixel for yuv420p.
RAM_BASE_MB = 120
RAM_OUT_FRAMES = 30
RAM_IN_FRAMES = 12

MIN_TRIM_SECONDS = 5
FPS_STEP_DOWN = 30  # high frame rate sources may be halved to this before lowering resolution
MAX_OUTPUT_FPS = 60
DEFAULT_FPS = 30.0


class PlanError(Exception):
    """A request that cannot be planned; `code` is machine-readable."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


@dataclass(frozen=True)
class PlannerConfig:
    min_bpp: float = 0.05
    container_overhead: float = 0.06
    audio_bitrates: Sequence[int] = (128, 96, 64, 48, 32)
    audio_share: float = 0.2
    resolution_ladder: Sequence[int] = (1080, 720, 540, 480, 360, 240, 144)
    preset_factors: Dict[str, float] = field(default_factory=lambda: {
        "ultrafast": 0.4, "superfast": 0.6, "veryfast": 1.0, "faster": 1.6,
    })
    decode_factors: Dict[str, float] = field(default_factory=lambda: {
        "h264": 0.15, "vp9": 0.3, "hevc": 0.35, "av1": 0.6, "default": 0.4,
    })
    base_preset: str = "veryfast"
    upgrade_preset: str = "faster"
    allowed_presets: Sequence[str] = ("ultrafast", "superfast", "veryfast", "faster")
    safety_factor: float = 1.75
    max_total_wait: float = 1800
    max_input_duration: float = 1200
    max_input_height: int = 1080
    max_input_bytes: int = 1500 * MB
    concurrency: int = 1
    max_queue_length: int = 5
    max_jobs_per_user: int = 1
    max_jobs_per_ip: int = 2
    disk_factor: float = 2.5
    min_free_disk_bytes: int = 2048 * 1024 * 1024
    min_free_ram_mb: int = 768

    @classmethod
    def from_settings(cls, s) -> "PlannerConfig":
        return cls(
            min_bpp=s.compression_min_bpp,
            container_overhead=s.compression_container_overhead,
            audio_bitrates=tuple(sorted(s.compression_audio_bitrates, reverse=True)),
            audio_share=s.compression_audio_share,
            resolution_ladder=tuple(sorted(s.compression_resolution_ladder, reverse=True)),
            preset_factors=dict(s.compression_preset_factors),
            decode_factors=dict(s.compression_decode_factors),
            base_preset=s.compression_preset,
            upgrade_preset=s.compression_upgrade_preset,
            allowed_presets=tuple(s.compression_allowed_presets),
            safety_factor=s.compression_safety_factor,
            max_total_wait=s.compression_max_total_wait_sec,
            max_input_duration=s.compression_max_input_duration_sec,
            max_input_height=s.compression_max_input_height,
            max_input_bytes=s.compression_max_input_file_mb * MB,
            concurrency=s.compression_concurrency,
            max_queue_length=s.compression_max_queue_length,
            max_jobs_per_user=s.compression_max_jobs_per_user,
            max_jobs_per_ip=s.compression_max_jobs_per_ip,
            disk_factor=s.compression_disk_factor,
            min_free_disk_bytes=s.compression_min_free_disk_mb * 1024 * 1024,
            min_free_ram_mb=s.compression_min_free_ram_mb,
        )

    def preset_factor(self, preset: str) -> float:
        return self.preset_factors.get(preset, 1.0)

    def decode_factor(self, codec: str) -> float:
        return self.decode_factors.get(codec, self.decode_factors.get("default", 0.4))


@dataclass(frozen=True)
class Perf:
    """Current performance model: veryfast-equivalent output px/s and two-pass ratio."""
    throughput: float
    two_pass_factor: float


@dataclass(frozen=True)
class SourceVideo:
    duration: Optional[float]
    width: int
    height: int
    fps: float
    vcodec: str  # normalized: h264 / vp9 / hevc / av1 / ...
    has_audio: bool
    size_bytes: Optional[int] = None
    format_spec: Optional[str] = None  # exact yt-dlp format ids, None = extractor default
    dims_known: bool = True

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass(frozen=True)
class TargetSpec:
    target_bytes: int
    max_height: Optional[int] = None  # output short side cap
    resolution: Optional[int] = None  # exact output short side the user asked for (None = auto)
    audio_kbps_cap: Optional[int] = None
    encoder_preset: Optional[str] = None  # explicit choice disables the auto upgrade
    trim_start: Optional[float] = None
    trim_end: Optional[float] = None


@dataclass(frozen=True)
class EncodePlan:
    duration: float  # after trimming
    target_bytes: int
    width: int
    height: int
    fps: float
    video_kbps: int
    audio_kbps: int  # 0 when the source has no audio
    bpp: float
    encoder_preset: str
    est_seconds: float  # both passes, without safety factor
    ram_mb: int

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)


@dataclass(frozen=True)
class QueueSnapshot:
    """Load state that admission is checked against."""
    remaining_seconds: Sequence[float] = ()  # raw estimates of admitted, unfinished jobs
    waiting_count: int = 0  # admitted jobs that are not encoding yet
    user_jobs: int = 0
    ip_jobs: int = 0
    free_disk_bytes: int = 10**12
    reserved_disk_bytes: int = 0
    ram_capacity_mb: int = 10**6  # memory the container may use in total


@dataclass
class Evaluation:
    ok: bool
    code: str = "ok"
    message: str = ""
    plan: Optional[EncodePlan] = None
    source: Optional[SourceVideo] = None
    queue_wait: float = 0.0
    total_seconds: float = 0.0  # queue wait + estimate * safety factor
    min_target_mb: Optional[float] = None
    deferred: bool = False

    def summary(self) -> dict:
        d = {
            "admitted": self.ok, "code": self.code, "message": self.message,
            "queue_wait_seconds": round(self.queue_wait),
            "total_seconds": round(self.total_seconds),
            "deferred": self.deferred,
        }
        if self.plan:
            p = self.plan
            d.update({
                "width": p.width, "height": p.height, "fps": p.fps,
                "video_kbps": p.video_kbps, "audio_kbps": p.audio_kbps,
                "encoder_preset": p.encoder_preset,
                "estimate_seconds": round(p.est_seconds),
                "duration": p.duration,
            })
        if self.source:
            s = self.source
            d["source"] = {
                "resolution": f"{s.width}x{s.height}" if s.dims_known else None,
                "vcodec": s.vcodec,
                "size_mb": round(s.size_bytes / MB, 1) if s.size_bytes else None,
            }
        if self.min_target_mb is not None:
            d["min_target_mb"] = self.min_target_mb
        return d


# ---------------------------------------------------------------- helpers

def normalize_codec(vcodec: Optional[str]) -> str:
    c = (vcodec or "").lower()
    if c.startswith(("avc", "h264")):
        return "h264"
    if c.startswith(("vp09", "vp9")):
        return "vp9"
    if c.startswith(("vp08", "vp8")):
        return "vp8"
    if c.startswith(("hev", "hvc", "h265")):
        return "hevc"
    if c.startswith(("av01", "av1")):
        return "av1"
    return c or "unknown"


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def effective_duration(duration: Optional[float], trim_start: Optional[float],
                       trim_end: Optional[float]) -> float:
    if duration is None:
        raise PlanError("unknown_duration",
                        "The video duration is unknown (live stream?), so a file size target can't be planned.")
    if duration <= 0:
        raise PlanError("zero_duration", "The video has zero duration.")
    start = trim_start or 0.0
    end = min(trim_end, duration) if trim_end is not None else duration
    if start >= duration or end - start <= 0:
        raise PlanError("invalid_trim", "The trim range is empty or outside the video.")
    return end - start


def scale_dims(width: int, height: int, short_side: int) -> tuple:
    """Scale to the given short side keeping aspect ratio; even dimensions for yuv420p."""
    src_short = min(width, height)
    if short_side >= src_short:
        w, h = width, height
    else:
        k = short_side / src_short
        w, h = width * k, height * k
    return max(2, int(round(w / 2)) * 2), max(2, int(round(h / 2)) * 2)


def fps_options(src_fps: float) -> List[float]:
    fps = min(src_fps or DEFAULT_FPS, MAX_OUTPUT_FPS)
    return [fps, FPS_STEP_DOWN] if fps > FPS_STEP_DOWN else [fps]


def ladder_for(src: SourceVideo, cap: Optional[int], cfg: PlannerConfig) -> List[int]:
    """Output short sides to try, highest first; never upscales."""
    top = min(src.short_side, cap or src.short_side)
    steps = [h for h in cfg.resolution_ladder if h <= top]
    if not steps or steps[0] < top:
        steps.insert(0, top)
    return steps


def total_kbps(target_bytes: int, duration: float, cfg: PlannerConfig) -> float:
    return target_bytes * 8 * (1 - cfg.container_overhead) / duration / 1000


def pick_audio_kbps(total: float, has_audio: bool, cap: Optional[int], cfg: PlannerConfig) -> int:
    if not has_audio:
        return 0
    steps = [a for a in cfg.audio_bitrates if cap is None or a <= cap] or [min(cfg.audio_bitrates)]
    for a in steps:
        if a <= total * cfg.audio_share:
            return a
    return steps[-1]


def pick_resolution(video_kbps: float, src: SourceVideo, cap: Optional[int], cfg: PlannerConfig,
                    fixed: bool = False):
    """Highest ladder step (then highest fps) whose bits per pixel stay above min_bpp.

    With `fixed` only the top step (the requested resolution) is tried; the
    frame rate may still drop.
    """
    steps = ladder_for(src, cap, cfg)
    for short in steps[:1] if fixed else steps:
        w, h = scale_dims(src.width, src.height, short)
        for fps in fps_options(src.fps):
            bpp = video_kbps * 1000 / (w * h * fps)
            if bpp >= cfg.min_bpp:
                return w, h, fps, bpp
    return None


def min_target_bytes(duration: float, src: SourceVideo, cfg: PlannerConfig,
                     short_side: Optional[int] = None, audio_cap: Optional[int] = None) -> int:
    """Smallest target that still fits the given resolution (default: the lowest ladder step).

    Audio is chosen as a share of the total, so a bigger total can claim a
    higher audio step; grow the total until the video part still fits.
    """
    w, h = scale_dims(src.width, src.height, short_side or ladder_for(src, None, cfg)[-1])
    fps = fps_options(src.fps)[-1]
    video = cfg.min_bpp * w * h * fps / 1000
    audio = pick_audio_kbps(0, src.has_audio, audio_cap, cfg)
    # The audio step only grows with the total, so this settles within len(audio_bitrates) rounds
    for _ in cfg.audio_bitrates:
        claimed = pick_audio_kbps(video + audio, src.has_audio, audio_cap, cfg)
        if claimed <= audio:
            break
        audio = claimed
    total = video + audio
    return int(math.ceil(total * 1000 * duration / 8 / (1 - cfg.container_overhead)))


def estimate_seconds(duration: float, src: SourceVideo, w: int, h: int, fps: float,
                     preset: str, perf: Perf, cfg: PlannerConfig) -> float:
    """Wall time for both passes: pass-2 work units / throughput * two-pass ratio."""
    work = pass2_work_units(duration, src, w, h, fps, preset, cfg)
    return work / perf.throughput * perf.two_pass_factor


def pass2_work_units(duration: float, src: SourceVideo, w: int, h: int, fps: float,
                     preset: str, cfg: PlannerConfig) -> float:
    """Cost of one encoding pass in veryfast-equivalent output pixels."""
    in_fps = min(src.fps or DEFAULT_FPS, 240)
    return duration * (fps * w * h * cfg.preset_factor(preset)
                       + in_fps * src.pixels * cfg.decode_factor(src.vcodec))


def estimate_ram_mb(src_pixels: int, out_pixels: int) -> int:
    return int(RAM_BASE_MB + (out_pixels * 1.5 * RAM_OUT_FRAMES + src_pixels * 1.5 * RAM_IN_FRAMES) / 1e6)


def estimate_source_bytes(src: SourceVideo, duration: float) -> int:
    """Known size scaled to the trimmed part, or a generous guess (0.1 bpp + audio)."""
    if src.size_bytes and src.duration:
        return int(src.size_bytes * min(1.0, duration / src.duration)) if duration else src.size_bytes
    return int(duration * (src.pixels * (src.fps or DEFAULT_FPS) * 0.1 + 160_000) / 8)


def plan_encode(src: SourceVideo, target: TargetSpec, preset: str, perf: Perf,
                cfg: PlannerConfig) -> EncodePlan:
    duration = effective_duration(src.duration, target.trim_start, target.trim_end)
    if duration > cfg.max_input_duration:
        raise PlanError(
            "too_long",
            f"The video is {format_duration(duration)} long; compression is limited to "
            f"{format_duration(cfg.max_input_duration)}. Trim it to fit.",
        )
    total = total_kbps(target.target_bytes, duration, cfg)
    audio = pick_audio_kbps(total, src.has_audio, target.audio_kbps_cap, cfg)
    video = total - audio
    caps = [c for c in (target.resolution, target.max_height) if c]
    cap = min(caps) if caps else None
    fixed = target.resolution is not None
    picked = pick_resolution(video, src, cap, cfg, fixed) if video > 0 else None
    if picked is None and fixed:
        short = ladder_for(src, cap, cfg)[0]
        need = math.ceil(min_target_bytes(duration, src, cfg, short, target.audio_kbps_cap) / MB * 10) / 10
        raise PlanError(
            "resolution_budget",
            f"{target.target_bytes / MB:g} MB is too small for {format_duration(duration)} at {short}p: "
            f"it needs at least {need:g} MB. Allow a bigger file, trim the video, or let the "
            f"resolution be picked automatically.",
            min_target_mb=need, resolution=short,
        )
    if picked is None:
        need = min_target_bytes(duration, src, cfg, audio_cap=target.audio_kbps_cap)
        raise PlanError(
            "budget_too_low",
            f"{target.target_bytes / MB:g} MB is too small for {format_duration(duration)} of video: "
            f"even at the lowest resolution it needs at least {math.ceil(need / MB * 10) / 10:g} MB.",
            min_target_mb=math.ceil(need / MB * 10) / 10,
        )
    w, h, fps, bpp = picked
    return EncodePlan(
        duration=duration,
        target_bytes=target.target_bytes,
        width=w, height=h, fps=fps,
        video_kbps=int(video), audio_kbps=audio, bpp=round(bpp, 4),
        encoder_preset=preset,
        est_seconds=estimate_seconds(duration, src, w, h, fps, preset, perf, cfg),
        ram_mb=estimate_ram_mb(src.pixels, w * h),
    )


# ---------------------------------------------------------------- source format choice

def _dims(fmt) -> Optional[tuple]:
    res = getattr(fmt, "resolution", None) or ""
    m = re.match(r"^(\d+)x(\d+)$", res)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+)p$", res)
    if m:
        h = int(m.group(1))
        return int(round(h * 16 / 9 / 2)) * 2, h
    return None


def _fmt_size(fmt, duration: Optional[float]) -> Optional[int]:
    size = fmt.filesize or fmt.filesize_approx
    if size:
        return int(size)
    if fmt.tbr and duration:
        return int(fmt.tbr * 1000 / 8 * duration)
    return None


def select_source(formats: Sequence, duration: Optional[float], cap_short: int,
                  prefer_h264: bool = False) -> Optional[SourceVideo]:
    """Pick the source formats to download for a given output short side.

    Highest resolution not above the cap (the encode downscales to it anyway),
    H.264 preferred at equal resolution because it is the cheapest to decode.
    With prefer_h264 the codec wins over resolution. Remaining ties follow
    yt-dlp's own order (worst to best), which already prefers https over
    HLS (often 403 on YouTube) and plain audio over DRC variants.
    """
    rank = {id(f): i for i, f in enumerate(formats)}
    videos = [f for f in formats if f.has_video and f.ext != "mhtml" and _dims(f)]
    if not videos:
        return None
    short = lambda f: min(_dims(f))  # noqa: E731
    is_h264 = lambda f: normalize_codec(f.vcodec) == "h264"  # noqa: E731
    pool = [f for f in videos if short(f) <= cap_short] or [min(videos, key=short)]
    if prefer_h264:
        key = lambda f: (is_h264(f), short(f), rank[id(f)])  # noqa: E731
    else:
        key = lambda f: (short(f), is_h264(f), rank[id(f)])  # noqa: E731
    video = max(pool, key=key)

    audio = None
    if not video.has_audio:
        audios = [f for f in formats if f.has_audio and not f.has_video]
        if audios:
            audio = max(audios, key=lambda f: (f.ext == "m4a", rank[id(f)]))

    w, h = _dims(video)
    sizes = [_fmt_size(f, duration) for f in (video, audio) if f is not None]
    return SourceVideo(
        duration=duration,
        width=w, height=h,
        fps=video.fps or DEFAULT_FPS,
        vcodec=normalize_codec(video.vcodec),
        has_audio=video.has_audio or audio is not None,
        size_bytes=sum(sizes) if all(sizes) else None,
        format_spec=f"{video.format_id}+{audio.format_id}" if audio else video.format_id,
    )


def has_h264_alternative(formats: Sequence, cap_short: int, current: SourceVideo) -> bool:
    if current.vcodec == "h264":
        return False
    return any(
        f.has_video and _dims(f) and min(_dims(f)) <= cap_short
        and normalize_codec(f.vcodec) == "h264"
        for f in formats
    )


def unknown_source(duration: Optional[float], cap_short: int) -> SourceVideo:
    """Worst-case stand-in when the extractor gives no format list."""
    short = cap_short
    return SourceVideo(
        duration=duration, width=int(round(short * 16 / 9 / 2)) * 2, height=short,
        fps=DEFAULT_FPS, vcodec="default", has_audio=True, dims_known=False,
    )


# ---------------------------------------------------------------- admission

def queue_wait_seconds(remaining: Sequence[float], cfg: PlannerConfig) -> float:
    return sum(r * cfg.safety_factor for r in remaining) / max(1, cfg.concurrency)


def check_admission(plan: EncodePlan, src: SourceVideo, snap: QueueSnapshot,
                    cfg: PlannerConfig, deferred: bool = False) -> Evaluation:
    ev = Evaluation(ok=False, plan=plan, source=src, deferred=deferred)
    own = plan.est_seconds * cfg.safety_factor
    ev.queue_wait = queue_wait_seconds(snap.remaining_seconds, cfg)
    ev.total_seconds = ev.queue_wait + own
    # The whole source is downloaded even when only part of it is kept
    input_bytes = estimate_source_bytes(src, src.duration or plan.duration)

    def reject(code, message):
        ev.code, ev.message = code, message
        return ev

    if snap.waiting_count >= cfg.max_queue_length:
        return reject("queue_full", "The compression queue is full. Please try again in a few minutes.")
    if snap.user_jobs >= cfg.max_jobs_per_user:
        return reject("user_limit",
                      f"You already have {snap.user_jobs} compression job(s) running; wait for it to finish.")
    if snap.ip_jobs >= cfg.max_jobs_per_ip:
        return reject("ip_limit", "Too many compression jobs from your network; wait for one to finish.")
    if input_bytes > cfg.max_input_bytes:
        return reject("input_too_large",
                      f"The source is about {input_bytes / MB:.0f} MB; the limit for compression is "
                      f"{cfg.max_input_bytes / MB:.0f} MB.")
    if snap.free_disk_bytes - snap.reserved_disk_bytes - cfg.min_free_disk_bytes < input_bytes * cfg.disk_factor:
        return reject("disk_low", "The server is low on disk space right now. Please try again later.")
    if plan.ram_mb + cfg.min_free_ram_mb > snap.ram_capacity_mb:
        return reject("ram_low", f"Encoding at {plan.width}x{plan.height} needs more memory than the server has.")
    if own > cfg.max_total_wait:
        return reject("too_slow",
                      f"Encoding would take about {format_duration(own)}, over the "
                      f"{format_duration(cfg.max_total_wait)} limit.")
    if not deferred and ev.total_seconds > cfg.max_total_wait:
        return reject("queue_busy",
                      f"The server is busy: waiting in the queue plus encoding would take about "
                      f"{format_duration(ev.total_seconds)}, over the {format_duration(cfg.max_total_wait)} limit.")
    ev.ok = True
    return ev


def evaluate(formats: Sequence, duration: Optional[float], quality_cap: Optional[int],
             target: TargetSpec, prefer_h264: bool, deferred: bool,
             snap: QueueSnapshot, perf: Perf, cfg: PlannerConfig) -> Evaluation:
    """Choose the source format and output plan, then run admission.

    The base preset is used unless the upgrade preset also passes admission
    (or the user picked a preset explicitly).
    """
    try:
        if target.encoder_preset and target.encoder_preset not in cfg.allowed_presets:
            raise PlanError("invalid_preset", f"Unknown encoder preset: {target.encoder_preset}")
        caps = [c for c in (quality_cap, target.max_height, target.resolution, cfg.max_input_height) if c]
        cap = min(caps)

        src = select_source(formats, duration, cap, prefer_h264) if formats else None
        if src is None:
            if formats and any(f.has_video for f in formats):
                raise PlanError("no_video_format", "No downloadable video format with a known resolution.")
            src = unknown_source(duration, cap)
        elif src.short_side > cfg.max_input_height:
            raise PlanError("source_too_large",
                            f"The smallest available format is {src.width}x{src.height}, above the "
                            f"{cfg.max_input_height}p limit for compression.")

        presets = [target.encoder_preset] if target.encoder_preset else \
            [p for p in (cfg.upgrade_preset, cfg.base_preset) if p]
        result = None
        for preset in presets:
            plan = plan_encode(src, target, preset, perf, cfg)
            # No point downloading more pixels than the encode keeps
            if formats and src.dims_known and plan.short_side < src.short_side:
                smaller = select_source(formats, duration, plan.short_side, prefer_h264)
                if smaller and smaller.short_side < src.short_side:
                    src_for_plan = smaller
                    plan = plan_encode(smaller, target, preset, perf, cfg)
                else:
                    src_for_plan = src
            else:
                src_for_plan = src
            result = check_admission(plan, src_for_plan, snap, cfg, deferred)
            if result.ok:
                return result
        return result
    except PlanError as e:
        ev = Evaluation(ok=False, code=e.code, message=e.message, deferred=deferred)
        ev.min_target_mb = e.extra.get("min_target_mb")
        return ev


# ---------------------------------------------------------------- alternatives

def build_alternatives(options: dict, ev: Evaluation, run: Callable[[dict], Evaluation],
                       cfg: PlannerConfig, duration: Optional[float],
                       h264_available: bool = False) -> List[dict]:
    """Ways to make a rejected request fit, ordered by increasing quality loss.

    `options` are the CompressionOptions as a dict, `run` re-evaluates modified
    options. Only alternatives that actually pass admission are returned, each
    carrying the full options to resubmit.
    """
    alts: List[dict] = []

    def offer(kind: str, label, opts: dict) -> bool:
        """`label` may be a callable taking the passing Evaluation."""
        r = run(opts)
        if not r.ok:
            return False
        if callable(label):
            label = label(r)
        alts.append({"kind": kind, "label": f"{label} (~{format_duration(r.total_seconds)})",
                     "options": opts, "estimate": r.summary()})
        return True

    code = ev.code
    if code == "too_long":
        trimmed = dict(options, trim_end=(options.get("trim_start") or 0) + cfg.max_input_duration)
        r = run(trimmed)
        label = f"Trim to {format_duration(cfg.max_input_duration)}"
        if r.ok:
            offer("trim", label, trimmed)
            return alts
        return [dict(a, label=f"{label}, {a['label'][0].lower()}{a['label'][1:]}")
                for a in build_alternatives(trimmed, r, run, cfg, duration, h264_available)
                if a["kind"] != "trim"]

    if code == "budget_too_low":
        if ev.min_target_mb:
            opts = dict(options, preset_id=None, target_size_mb=ev.min_target_mb)
            offer("bigger_target", f"Use {ev.min_target_mb:g} MB instead", opts)
        _offer_trim(options, run, offer, duration)
        return alts

    if code == "resolution_budget":
        # The user asked for this resolution: keep it and spend size or length instead
        if ev.min_target_mb:
            opts = dict(options, preset_id=None, target_size_mb=ev.min_target_mb)
            offer("bigger_target", f"Use {ev.min_target_mb:g} MB for {options.get('resolution')}p", opts)
        _offer_trim(options, run, offer, duration)
        offer("auto_resolution", lambda r: f"Auto resolution ({r.plan.short_side}p)",
              dict(options, resolution=None))
        return alts

    if code not in ("too_slow", "queue_busy", "ram_low", "input_too_large", "disk_low"):
        return alts

    if h264_available and not options.get("prefer_h264"):
        offer("h264", "Download the H.264 stream (cheaper to decode)", dict(options, prefer_h264=True))

    current = ev.plan.short_side if ev.plan else cfg.max_input_height
    for h in cfg.resolution_ladder:
        if h < current and offer("resolution", f"Encode at {h}p", dict(options, resolution=h)):
            break

    current_factor = cfg.preset_factor(ev.plan.encoder_preset if ev.plan else cfg.base_preset)
    faster = sorted((p for p in cfg.allowed_presets if cfg.preset_factor(p) < current_factor),
                    key=cfg.preset_factor, reverse=True)
    for p in faster:
        if offer("preset", f"Encode faster ({p}, lower quality)", dict(options, encoder_preset=p)):
            break

    if code in ("too_slow", "queue_busy"):
        _offer_trim(options, run, offer, duration)

    if code == "queue_busy" and not options.get("deferred"):
        offer("deferred", "Queue it to run when the server is free", dict(options, deferred=True))
    return alts


def _offer_trim(options: dict, run: Callable[[dict], Evaluation], offer,
                duration: Optional[float]) -> None:
    """Longest clip from trim_start that passes; binary search on the length."""
    if not duration:
        return
    start = options.get("trim_start") or 0
    end = min(options.get("trim_end") or duration, duration)
    full = end - start
    if full <= MIN_TRIM_SECONDS:
        return
    lo, hi = 0.0, float(full)
    for _ in range(12):
        mid = (lo + hi) / 2
        if mid < MIN_TRIM_SECONDS:
            lo = mid
            break
        if run(dict(options, trim_end=start + mid)).ok:
            lo = mid
        else:
            hi = mid
    length = math.floor(lo)
    if length >= MIN_TRIM_SECONDS and length < full:
        offer("trim", f"Trim to {format_duration(length)}", dict(options, trim_end=start + length))
