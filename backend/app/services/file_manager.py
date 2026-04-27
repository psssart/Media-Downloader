import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
import logging

from ..config import settings
from ..models import DownloadedFile

logger = logging.getLogger(__name__)


class FileManager:
    """Manages downloaded files and cleanup."""

    def __init__(self, downloads_dir: Path = None):
        self.downloads_dir = downloads_dir or settings.downloads_dir

    def get_files(self) -> List[DownloadedFile]:
        """Get list of downloaded files."""
        files = []

        if not self.downloads_dir.exists():
            return files

        for filepath in self.downloads_dir.iterdir():
            if filepath.is_file() and not filepath.name.startswith("."):
                stat = filepath.stat()
                files.append(DownloadedFile(
                    filename=filepath.name,
                    filepath=str(filepath),
                    size=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime),
                    download_url=f"/api/downloads/files/{filepath.name}",
                ))

        # Sort by creation time, newest first
        files.sort(key=lambda x: x.created_at, reverse=True)
        return files

    def get_file_path(self, filename: str) -> Path | None:
        """Get full path for a filename."""
        filepath = self.downloads_dir / filename

        # Security: ensure path is within downloads directory
        try:
            filepath.resolve().relative_to(self.downloads_dir.resolve())
        except ValueError:
            logger.warning(f"Attempted path traversal: {filename}")
            return None

        if filepath.exists() and filepath.is_file():
            return filepath

        return None

    def delete_file(self, filename: str) -> bool:
        """Delete a file."""
        filepath = self.get_file_path(filename)

        if filepath:
            try:
                filepath.unlink()
                logger.info(f"Deleted file: {filename}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete {filename}: {e}")

        return False

    def cleanup_old_files(self, retention_hours: int = None):
        """Remove files older than retention period."""
        retention = retention_hours or settings.file_retention_hours
        cutoff = datetime.now() - timedelta(hours=retention)

        deleted_count = 0

        for filepath in self.downloads_dir.iterdir():
            if filepath.is_file():
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)

                if mtime < cutoff:
                    try:
                        filepath.unlink()
                        deleted_count += 1
                        logger.info(f"Cleaned up old file: {filepath.name}")
                    except Exception as e:
                        logger.error(f"Failed to cleanup {filepath.name}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleanup completed: {deleted_count} files removed")

        return deleted_count

    def get_storage_stats(self) -> dict:
        """Get storage statistics."""
        total_size = 0
        file_count = 0

        for filepath in self.downloads_dir.iterdir():
            if filepath.is_file():
                total_size += filepath.stat().st_size
                file_count += 1

        return {
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": file_count,
        }


# Singleton instance
file_manager = FileManager()
