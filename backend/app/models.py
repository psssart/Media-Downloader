from pydantic import BaseModel, Field, HttpUrl, model_validator
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    TRIMMING = "trimming"  # cutting the clip out of a download that isn't compressed
    QUEUED = "queued"  # downloaded, waiting for a compression slot
    COMPRESSING = "compressing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class FormatInfo(BaseModel):
    """Video/audio format information."""
    format_id: str
    ext: str
    resolution: Optional[str] = None
    filesize: Optional[int] = None
    filesize_approx: Optional[int] = None
    fps: Optional[float] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    abr: Optional[float] = None
    vbr: Optional[float] = None
    tbr: Optional[float] = None
    format_note: Optional[str] = None
    quality_label: Optional[str] = None
    has_video: bool = True
    has_audio: bool = True


class MediaInfo(BaseModel):
    """Extracted media information."""
    id: str
    title: str
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    uploader: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    webpage_url: str
    extractor: str
    formats: List[FormatInfo] = []
    best_video_format: Optional[FormatInfo] = None
    best_audio_format: Optional[FormatInfo] = None
    media_type: str = "video"
    source_url: Optional[str] = None
    entries: List["MediaInfo"] = []


class URLRequest(BaseModel):
    """Request to extract media info."""
    url: HttpUrl


class CompressionOptions(BaseModel):
    """Compress the downloaded video to a target file size."""
    preset_id: Optional[str] = None  # platform preset; None means custom target_size_mb
    target_size_mb: Optional[float] = Field(None, gt=0, le=100000)
    # Overrides used by admission alternatives
    max_height: Optional[int] = Field(None, gt=0)  # output short side cap
    resolution: Optional[int] = Field(None, gt=0)  # exact output short side; None = auto
    prefer_h264: bool = False
    encoder_preset: Optional[str] = None
    trim_start: Optional[float] = Field(None, ge=0)
    trim_end: Optional[float] = Field(None, gt=0)
    deferred: bool = False


class DownloadRequest(BaseModel):
    """Request to download media."""
    url: HttpUrl
    format_id: Optional[str] = None
    quality: Optional[str] = "best"  # best, 1080p, 720p, 480p, audio_only
    audio_only: bool = False
    media_type: str = "video"
    source_url: Optional[str] = None
    title: Optional[str] = None
    compression: Optional[CompressionOptions] = None  # off unless set
    # Keep only this part of the media (seconds). With compression the range is
    # handed to the encoder (exact cut), otherwise the file is cut by stream copy.
    trim_start: Optional[float] = Field(None, ge=0)
    trim_end: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _check_trim(self):
        if self.trim_end is not None and self.trim_end <= (self.trim_start or 0):
            raise ValueError("trim_end must be after trim_start")
        return self

    @property
    def has_trim(self) -> bool:
        return bool(self.trim_start) or self.trim_end is not None


class CompressionEstimateRequest(BaseModel):
    """Dry-run admission check for a compression download."""
    url: HttpUrl
    quality: Optional[str] = "best"
    compression: CompressionOptions


class CompressionResult(BaseModel):
    """Final compression report."""
    target_mb: float
    actual_mb: float
    width: int
    height: int
    fps: float
    encoder_preset: str
    retries: int = 0
    passthrough: bool = False  # source already fit the target, no re-encode


class DownloadTask(BaseModel):
    """Download task status."""
    task_id: str
    client_id: str
    url: str
    title: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    filename: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    # Compression (only set for compression tasks)
    compression_target_mb: Optional[float] = None
    compression_pass: Optional[int] = None  # 1 or 2 while encoding
    queue_position: Optional[int] = None  # 1-based while QUEUED
    estimate_seconds: Optional[float] = None
    compression_result: Optional[CompressionResult] = None


class DownloadedFile(BaseModel):
    """Downloaded file info."""
    filename: str
    filepath: str
    size: int
    created_at: datetime
    download_url: str
    thumbnail_url: Optional[str] = None


class CookieUpload(BaseModel):
    """Cookie content upload."""
    content: str
    name: Optional[str] = "cookies.txt"


class SettingsResponse(BaseModel):
    """Current settings."""
    cookies_configured: bool
    file_retention_hours: float
    max_concurrent_downloads: int
