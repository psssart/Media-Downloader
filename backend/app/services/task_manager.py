import asyncio
import uuid
from datetime import datetime
from typing import Dict, Optional, Callable
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from ..models import DownloadTask, TaskStatus, DownloadRequest
from .ytdlp_wrapper import ytdlp_wrapper, YTDLPWrapper

logger = logging.getLogger(__name__)


class TaskManager:
    """Manages download tasks with background processing."""

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or settings.max_concurrent_downloads
        self.tasks: Dict[str, DownloadTask] = {}
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._lock = asyncio.Lock()

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        """Create a new download task."""
        task_id = str(uuid.uuid4())[:8]

        task = DownloadTask(
            task_id=task_id,
            url=str(request.url),
            status=TaskStatus.PENDING,
            created_at=datetime.now(),
        )

        async with self._lock:
            self.tasks[task_id] = task

        # Start background download
        asyncio.get_event_loop().run_in_executor(
            self.executor,
            self._process_download,
            task_id,
            request,
        )

        return task

    def _process_download(self, task_id: str, request: DownloadRequest):
        """Process download in background thread."""
        task = self.tasks.get(task_id)
        if not task:
            return

        try:
            # Update status to processing
            task.status = TaskStatus.PROCESSING

            # First extract info to get title
            try:
                info = ytdlp_wrapper.extract_info(str(request.url))
                task.title = info.title
            except Exception as e:
                logger.warning(f"Could not extract title: {e}")

            # Update status to downloading
            task.status = TaskStatus.DOWNLOADING

            # Create progress hook
            def progress_hook(d):
                if d["status"] == "downloading":
                    # Parse progress
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    downloaded = d.get("downloaded_bytes", 0)

                    if total > 0:
                        task.progress = (downloaded / total) * 100

                    task.speed = d.get("_speed_str", "")
                    task.eta = d.get("_eta_str", "")

                elif d["status"] == "finished":
                    task.status = TaskStatus.MERGING
                    task.progress = 100

            # Determine format
            if request.audio_only:
                filepath = ytdlp_wrapper.download_audio(
                    str(request.url),
                    settings.downloads_dir,
                    progress_hook=progress_hook,
                )
            else:
                format_spec = None
                if request.format_id:
                    format_spec = request.format_id
                elif request.quality:
                    format_spec = YTDLPWrapper.get_format_for_quality(request.quality)

                filepath = ytdlp_wrapper.download(
                    str(request.url),
                    settings.downloads_dir,
                    format_spec=format_spec,
                    progress_hook=progress_hook,
                )

            # Update task with completion info
            task.status = TaskStatus.COMPLETED
            task.progress = 100
            task.filename = Path(filepath).name
            task.completed_at = datetime.now()

            logger.info(f"Task {task_id} completed: {task.filename}")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)

    async def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Get task by ID."""
        return self.tasks.get(task_id)

    async def get_all_tasks(self) -> list[DownloadTask]:
        """Get all tasks."""
        return list(self.tasks.values())

    async def get_active_tasks(self) -> list[DownloadTask]:
        """Get active (non-completed) tasks."""
        return [
            t for t in self.tasks.values()
            if t.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED]
        ]

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        async with self._lock:
            if task_id in self.tasks:
                del self.tasks[task_id]
                return True
        return False

    async def clear_completed(self):
        """Clear all completed tasks."""
        async with self._lock:
            self.tasks = {
                k: v for k, v in self.tasks.items()
                if v.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED]
            }


# Singleton instance
task_manager = TaskManager()
