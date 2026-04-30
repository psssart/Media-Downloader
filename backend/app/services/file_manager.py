import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote
import logging
import mimetypes

from ..config import settings
from ..models import DownloadedFile

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".flac", ".m4a", ".ogg"}


class FileManager:
    """Manages downloaded files and cleanup."""

    def __init__(self, downloads_dir: Path = None):
        self.downloads_dir = downloads_dir or settings.downloads_dir

    def _user_dir(self, client_id: str) -> Path:
        """Get the per-user download directory."""
        return self.downloads_dir / client_id

    def _is_thumbnail(self, filepath: Path) -> bool:
        """Check if a file is a thumbnail image (not a standalone photo).

        An image file is a thumbnail only if a companion media file
        with the same stem exists in the same directory.
        """
        if filepath.suffix.lower() not in IMAGE_EXTENSIONS:
            return False
        # Check if a companion media file exists with the same stem
        for ext in MEDIA_EXTENSIONS:
            companion = filepath.with_suffix(ext)
            if companion.exists():
                return True
        return False

    def _find_thumbnail(self, filepath: Path, client_id: str) -> Optional[str]:
        """Find a matching thumbnail for a media file."""
        for ext in IMAGE_EXTENSIONS:
            thumb_path = filepath.with_suffix(ext)
            if thumb_path.exists() and thumb_path != filepath:
                return f"/api/downloads/thumbnails/{quote(thumb_path.name)}?client_id={client_id}"
        return None

    def get_files(self, client_id: str) -> List[DownloadedFile]:
        """Get list of downloaded files for a client."""
        files = []
        user_dir = self._user_dir(client_id)

        if not user_dir.exists():
            return files

        for filepath in user_dir.iterdir():
            if filepath.is_file() and not filepath.name.startswith("."):
                # Skip thumbnail images from the listing
                if self._is_thumbnail(filepath):
                    continue

                stat = filepath.stat()
                files.append(DownloadedFile(
                    filename=filepath.name,
                    filepath=str(filepath),
                    size=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime),
                    download_url=f"/api/downloads/files/{filepath.name}",
                    thumbnail_url=self._find_thumbnail(filepath, client_id),
                ))

        # Sort by creation time, newest first
        files.sort(key=lambda x: x.created_at, reverse=True)
        return files

    def get_file_path(self, filename: str, client_id: str) -> Path | None:
        """Get full path for a filename within a client's directory."""
        user_dir = self._user_dir(client_id)
        filepath = user_dir / filename

        # Security: ensure path is within user's directory
        try:
            filepath.resolve().relative_to(user_dir.resolve())
        except ValueError:
            logger.warning(f"Attempted path traversal: {filename} for client {client_id}")
            return None

        if filepath.exists() and filepath.is_file():
            return filepath

        return None

    def get_thumbnail_path(self, filename: str, client_id: str) -> Optional[Path]:
        """Get full path for a thumbnail file within a client's directory."""
        user_dir = self._user_dir(client_id)
        filepath = user_dir / filename

        # Security: ensure path is within user's directory
        try:
            filepath.resolve().relative_to(user_dir.resolve())
        except ValueError:
            logger.warning(f"Attempted path traversal: {filename} for client {client_id}")
            return None

        if filepath.exists() and filepath.is_file() and self._is_thumbnail(filepath):
            return filepath

        return None

    def delete_file(self, filename: str, client_id: str) -> bool:
        """Delete a file and its associated thumbnail."""
        filepath = self.get_file_path(filename, client_id)

        if filepath:
            try:
                # Also delete associated thumbnail
                for ext in IMAGE_EXTENSIONS:
                    thumb = filepath.with_suffix(ext)
                    if thumb.exists() and thumb != filepath:
                        thumb.unlink()
                        logger.info(f"Deleted thumbnail: {thumb.name}")

                filepath.unlink()
                logger.info(f"Deleted file: {filename}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete {filename}: {e}")

        return False

    def cleanup_old_files(self, retention_hours: float = None):
        """Remove files older than retention period across all user directories."""
        retention = retention_hours or settings.file_retention_hours
        cutoff = datetime.now() - timedelta(hours=retention)

        deleted_count = 0

        if not self.downloads_dir.exists():
            return 0

        for entry in self.downloads_dir.iterdir():
            if entry.is_file():
                # Legacy files in root downloads dir
                mtime = datetime.fromtimestamp(entry.stat().st_mtime)
                if mtime < cutoff:
                    try:
                        entry.unlink()
                        deleted_count += 1
                        logger.info(f"Cleaned up legacy file: {entry.name}")
                    except Exception as e:
                        logger.error(f"Failed to cleanup {entry.name}: {e}")
                continue

            if not entry.is_dir():
                continue

            # Process files within user subdirectory
            for filepath in entry.iterdir():
                if filepath.is_file():
                    mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                    if mtime < cutoff:
                        try:
                            filepath.unlink()
                            deleted_count += 1
                            logger.info(f"Cleaned up old file: {entry.name}/{filepath.name}")
                        except Exception as e:
                            logger.error(f"Failed to cleanup {filepath.name}: {e}")

            # Remove empty user directories
            try:
                if entry.exists() and not any(entry.iterdir()):
                    entry.rmdir()
                    logger.info(f"Removed empty user directory: {entry.name}")
            except Exception as e:
                logger.error(f"Failed to remove empty dir {entry.name}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleanup completed: {deleted_count} files removed")

        return deleted_count

    def get_storage_stats(self, client_id: str) -> dict:
        """Get storage statistics for a client."""
        user_dir = self._user_dir(client_id)
        total_size = 0
        file_count = 0

        if user_dir.exists():
            for filepath in user_dir.iterdir():
                if filepath.is_file():
                    # Skip thumbnails from stats
                    if self._is_thumbnail(filepath):
                        continue
                    total_size += filepath.stat().st_size
                    file_count += 1

        return {
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": file_count,
        }


# Singleton instance
file_manager = FileManager()
