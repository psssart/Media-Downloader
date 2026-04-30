from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from enum import Enum
from datetime import datetime


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


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


class DownloadRequest(BaseModel):
    """Request to download media."""
    url: HttpUrl
    format_id: Optional[str] = None
    quality: Optional[str] = "best"  # best, 1080p, 720p, 480p, audio_only
    audio_only: bool = False
    media_type: str = "video"
    source_url: Optional[str] = None
    title: Optional[str] = None


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
