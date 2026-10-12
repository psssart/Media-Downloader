"""Encoder throughput model for compression cost estimates.

Throughput is measured in veryfast-equivalent output pixels per second for one
pass-2 run with the configured thread count. It comes from a short synthetic
calibration encode at startup and is refined by every finished job. Samples
are normalized to "no CPU steal" and scaled back down by the recent steal
level when estimating, so a noisy shared vCPU makes estimates more pessimistic,
never more optimistic.
"""

import json
import logging
import os
import statistics
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Optional

from ..config import settings
from .compression_planner import Perf

logger = logging.getLogger(__name__)

CALIBRATION_SIZE = (1280, 720)
CALIBRATION_FPS = 30
CALIBRATION_SECONDS = 4
CALIBRATION_RESET_RATIO = 0.3  # recalibration this far off the stored one drops old samples
FALLBACK_THROUGHPUT = 10_000_000  # px/s, deliberately pessimistic, used if calibration fails
MIN_SAMPLES = 3
EWMA_ALPHA = 0.3
TWO_PASS_BOUNDS = (1.1, 3.0)


def read_cpu_times() -> Optional[tuple]:
    """(steal, total) jiffies from /proc/stat, None where unavailable."""
    try:
        with open("/proc/stat") as f:
            fields = [int(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    # user nice system idle iowait irq softirq steal (guest time is already in user)
    total = sum(fields[:8])
    steal = fields[7] if len(fields) > 7 else 0
    return steal, total


def steal_between(a: Optional[tuple], b: Optional[tuple]) -> float:
    if not a or not b or b[1] <= a[1]:
        return 0.0
    return max(0.0, min(1.0, (b[0] - a[0]) / (b[1] - a[1])))


def percentile(values, q: float) -> float:
    data = sorted(values)
    if not data:
        raise ValueError("empty")
    k = (len(data) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


class StealMonitor:
    """Tracks CPU steal over recent sampling windows (sampled by the scheduler)."""

    def __init__(self, window: int = 20):
        self._last = read_cpu_times()
        self.recent: Deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()

    def sample(self) -> None:
        now = read_cpu_times()
        with self._lock:
            if self._last and now:
                self.recent.append(steal_between(self._last, now))
            self._last = now

    def recent_max(self) -> float:
        with self._lock:
            return max(self.recent, default=0.0)


class ThroughputModel:
    def __init__(self, state_file: Optional[Path] = None, cfg=settings):
        self.cfg = cfg
        self.state_file = state_file or cfg.compression_state_dir / "calibration.json"
        self.steal = StealMonitor()
        self._lock = threading.Lock()
        self.calibration: Optional[float] = None
        self.samples: Deque[float] = deque(maxlen=20)
        self.ewma: Optional[float] = None
        self.two_pass_samples: Deque[float] = deque(maxlen=20)
        self.two_pass_ewma: float = cfg.compression_two_pass_factor
        self._load()

    # ------------------------------------------------------------ persistence

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        self.calibration = data.get("calibration")
        self.samples.extend(data.get("samples", []))
        self.ewma = data.get("ewma")
        self.two_pass_samples.extend(data.get("two_pass_samples", []))
        self.two_pass_ewma = data.get("two_pass_ewma") or self.two_pass_ewma

    def _save(self) -> None:
        data = {
            "calibration": self.calibration,
            "samples": list(self.samples),
            "ewma": self.ewma,
            "two_pass_samples": list(self.two_pass_samples),
            "two_pass_ewma": self.two_pass_ewma,
            "updated": time.time(),
        }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.state_file)
        except OSError as e:
            logger.warning(f"Could not save calibration state: {e}")

    # ------------------------------------------------------------ calibration

    def calibrate(self) -> Optional[float]:
        """Encode a synthetic clip and record raw (steal-free) throughput."""
        if self.cfg.compression_throughput_override:
            logger.info("Compression throughput override set, calibration skipped")
            return None
        w, h = CALIBRATION_SIZE
        frames = CALIBRATION_FPS * CALIBRATION_SECONDS
        src = ["-f", "lavfi", "-i",
               f"testsrc2=size={w}x{h}:rate={CALIBRATION_FPS}", "-frames:v", str(frames)]
        threads = str(self.cfg.compression_ffmpeg_threads)
        try:
            # Generating the test pattern has a cost of its own: measure it and subtract
            t_gen, _ = self._timed(src + ["-c:v", "rawvideo", "-f", "null", os.devnull])
            t_enc, steal = self._timed(src + [
                "-c:v", "libx264", "-preset", self.cfg.compression_preset, "-threads", threads,
                "-b:v", "2000k", "-pix_fmt", "yuv420p", "-f", "null", os.devnull,
            ])
        except Exception as e:
            logger.warning(f"Compression calibration failed: {e}")
            return None
        t = t_enc - t_gen if t_enc > t_gen * 1.2 else t_enc
        factor = self.cfg.compression_preset_factors.get(self.cfg.compression_preset, 1.0)
        raw = w * h * frames * factor / t / max(0.1, 1 - steal)
        with self._lock:
            if self.calibration and abs(raw / self.calibration - 1) > CALIBRATION_RESET_RATIO:
                logger.info("Calibration changed significantly (hardware change?), dropping old samples")
                self.samples.clear()
                self.ewma = None
            self.calibration = raw
            self._save()
        logger.info(f"Compression calibration: {raw / 1e6:.1f} Mpx/s (encode {t_enc:.2f}s, "
                    f"generate {t_gen:.2f}s, steal {steal:.0%})")
        return raw

    @staticmethod
    def _timed(args) -> tuple:
        cmd = ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
        before = read_cpu_times()
        start = time.monotonic()
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        elapsed = time.monotonic() - start
        return elapsed, steal_between(before, read_cpu_times())

    # ------------------------------------------------------------ refinement

    def observe(self, work_units: float, pass1_seconds: float, pass2_seconds: float,
                steal: float) -> None:
        """Record a finished two-pass job (work units of one pass-2 run)."""
        if pass2_seconds <= 0 or work_units <= 0:
            return
        raw = work_units / pass2_seconds / max(0.1, 1 - steal)
        with self._lock:
            self.samples.append(raw)
            self.ewma = raw if self.ewma is None else EWMA_ALPHA * raw + (1 - EWMA_ALPHA) * self.ewma
            if pass1_seconds > 0:
                ratio = (pass1_seconds + pass2_seconds) / pass2_seconds
                ratio = min(max(ratio, TWO_PASS_BOUNDS[0]), TWO_PASS_BOUNDS[1])
                self.two_pass_samples.append(ratio)
                self.two_pass_ewma = EWMA_ALPHA * ratio + (1 - EWMA_ALPHA) * self.two_pass_ewma
            self._save()

    # ------------------------------------------------------------ estimates

    def base_throughput(self) -> float:
        """Conservative steal-free throughput."""
        if self.cfg.compression_throughput_override:
            return float(self.cfg.compression_throughput_override)
        with self._lock:
            samples = list(self.samples)
            if len(samples) >= MIN_SAMPLES:
                return min(self.ewma or samples[-1], percentile(samples, 0.2))
            if self.calibration:
                t = self.calibration * self.cfg.compression_calibration_discount
                # A few real jobs slower than the synthetic clip override it
                return min([t, *samples])
            return min([FALLBACK_THROUGHPUT, *samples])

    def two_pass_factor(self) -> float:
        with self._lock:
            if len(self.two_pass_samples) >= MIN_SAMPLES:
                return max(self.two_pass_ewma, percentile(self.two_pass_samples, 0.8))
            return self.cfg.compression_two_pass_factor

    def perf(self) -> Perf:
        steal = 0.0 if self.cfg.compression_throughput_override else self.steal.recent_max()
        return Perf(throughput=self.base_throughput() * (1 - min(steal, 0.9)),
                    two_pass_factor=self.two_pass_factor())

    def snapshot(self) -> dict:
        perf = self.perf()
        return {
            "throughput_mpx_s": round(perf.throughput / 1e6, 2),
            "base_throughput_mpx_s": round(self.base_throughput() / 1e6, 2),
            "calibration_mpx_s": round(self.calibration / 1e6, 2) if self.calibration else None,
            "samples": len(self.samples),
            "two_pass_factor": round(perf.two_pass_factor, 3),
            "recent_steal": round(self.steal.recent_max(), 3),
            "override": self.cfg.compression_throughput_override,
        }
