from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Dict, List, Optional


class Settings(BaseSettings):
    """Application configuration settings."""

    # App settings
    app_name: str = "Media Downloader"
    debug: bool = False

    # Paths
    downloads_dir: Path = Path("/app/downloads")
    cookies_dir: Path = Path("/app/cookies")

    # Cleanup settings
    file_retention_hours: float = 0.5  # 30 minutes
    cleanup_interval_minutes: int = 10

    # Download settings
    max_concurrent_downloads: int = 3
    default_format: str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    # Cookie file path
    cookies_file: Optional[Path] = None

    # --- Video compression (opt-in per download) ---
    # Defaults are tuned for 1 vCPU / 3.5 GB RAM / 20 GB disk. See README
    # "Video compression" for how to scale them to other hardware.
    compression_enabled: bool = True
    # Platform presets live in a data file, so changed platform limits need no code change
    compression_presets_file: Path = Path(__file__).parent / "compression_presets.json"
    compression_encoder: str = "libx264"  # only libx264 is implemented
    compression_preset: str = "veryfast"  # base x264 preset
    # Used instead of the base preset only when its estimate still passes admission;
    # empty string disables the upgrade
    compression_upgrade_preset: str = "faster"
    # Presets a user may pick explicitly (as an "encode faster" alternative)
    compression_allowed_presets: List[str] = ["ultrafast", "superfast", "veryfast", "faster"]
    # Relative x264 cost per output pixel, veryfast = 1.0
    compression_preset_factors: Dict[str, float] = {
        "ultrafast": 0.4, "superfast": 0.6, "veryfast": 1.0,
        "faster": 1.6, "fast": 2.0, "medium": 2.6,
    }
    # Relative cost of decoding one input pixel vs encoding one output pixel at veryfast
    compression_decode_factors: Dict[str, float] = {
        "h264": 0.15, "vp8": 0.25, "vp9": 0.3, "hevc": 0.35, "av1": 0.6, "default": 0.4,
    }
    compression_cpu_cores: int = 1
    compression_max_concurrent_jobs: Optional[int] = None  # default: cpu_cores
    compression_threads: Optional[int] = None  # ffmpeg -threads per job; default: cores / jobs
    compression_safety_factor: float = 1.75
    compression_max_total_wait_sec: int = 1800  # queue wait + processing
    compression_max_queue_length: int = 5  # admitted jobs that are not encoding yet
    compression_max_jobs_per_user: int = 1
    compression_max_jobs_per_ip: int = 2
    # Hard guards; the effective limit is whatever passes the cost admission
    compression_max_input_duration_sec: int = 1200  # applied after trimming
    compression_max_input_height: int = 1080  # short side of the source
    compression_max_input_file_mb: int = 1500
    # Encode throughput in output px/s at veryfast; when set, calibration is disabled
    compression_throughput_override: Optional[float] = None
    # Synthetic calibration clips are easier than real video, discount until real samples exist
    compression_calibration_discount: float = 0.8
    # (pass1 + pass2) / pass2 time ratio; starting value, refined from real jobs
    compression_two_pass_factor: float = 1.6
    compression_min_free_disk_mb: int = 2048
    compression_min_free_ram_mb: int = 768
    compression_disk_factor: float = 2.5  # free disk required per byte of input
    compression_min_bpp: float = 0.05  # minimum video bits per pixel per frame
    compression_container_overhead: float = 0.06  # bitrate reserve for muxing/rate control error
    compression_audio_bitrates: List[int] = [128, 96, 64, 48, 32]  # kbps, highest first
    compression_audio_share: float = 0.2  # max share of the total bitrate given to audio
    compression_resolution_ladder: List[int] = [1080, 720, 540, 480, 360, 240, 144]  # short side
    compression_max_retries: int = 2  # extra pass-2 runs when the output overshoots the target
    compression_timeout_multiplier: float = 3.0  # ffmpeg timeout = estimate * multiplier
    compression_min_timeout_sec: int = 120
    compression_ram_wait_sec: int = 120  # how long a job waits for free RAM before failing

    @property
    def compression_concurrency(self) -> int:
        return max(1, self.compression_max_concurrent_jobs or self.compression_cpu_cores)

    @property
    def compression_ffmpeg_threads(self) -> int:
        if self.compression_threads:
            return self.compression_threads
        return max(1, self.compression_cpu_cores // self.compression_concurrency)

    @property
    def compression_work_dir(self) -> Path:
        # Dot-prefixed dirs are skipped by the file listing and the retention cleanup
        return self.downloads_dir / ".work"

    @property
    def compression_state_dir(self) -> Path:
        return self.downloads_dir / ".compression"

    class Config:
        env_file = ".env"
        env_prefix = "MD_"


settings = Settings()

# Ensure directories exist
settings.downloads_dir.mkdir(parents=True, exist_ok=True)
settings.cookies_dir.mkdir(parents=True, exist_ok=True)
