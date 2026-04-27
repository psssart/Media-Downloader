from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional


class Settings(BaseSettings):
    """Application configuration settings."""

    # App settings
    app_name: str = "Media Downloader"
    debug: bool = False

    # Paths
    downloads_dir: Path = Path("/app/downloads")
    cookies_dir: Path = Path("/app/cookies")

    # Cleanup settings (in hours)
    file_retention_hours: int = 24
    cleanup_interval_minutes: int = 30

    # Download settings
    max_concurrent_downloads: int = 3
    default_format: str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    # Cookie file path
    cookies_file: Optional[Path] = None

    class Config:
        env_file = ".env"
        env_prefix = "MD_"


settings = Settings()

# Ensure directories exist
settings.downloads_dir.mkdir(parents=True, exist_ok=True)
settings.cookies_dir.mkdir(parents=True, exist_ok=True)
