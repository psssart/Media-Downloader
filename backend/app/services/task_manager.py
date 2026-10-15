import asyncio
import re
import shutil
import threading
import uuid
from datetime import datetime
from typing import Dict, Optional, Callable
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from ..models import DownloadTask, TaskStatus, DownloadRequest, TERMINAL_STATUSES
from .ytdlp_wrapper import ytdlp_wrapper, YTDLPWrapper
from .compressor import compressor, quality_cap
from .trimmer import deliver_trimmed

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):
    pass


class TaskManager:
    """Manages download tasks with background processing."""

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or settings.max_concurrent_downloads
        self.tasks: Dict[str, DownloadTask] = {}
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        # Cancel flags of plain (non-compression) downloads, set by cancel_task
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = asyncio.Lock()

    async def create_task(self, request: DownloadRequest, client_id: str,
                          client_ip: Optional[str] = None) -> DownloadTask:
        """Create a new download task.

        With compression, admission runs first (from yt-dlp metadata, before
        anything is downloaded) and raises AdmissionRejected if it fails.
        """
        task_id = str(uuid.uuid4())[:8]

        task = DownloadTask(
            task_id=task_id,
            client_id=client_id,
            url=str(request.url),
            status=TaskStatus.PENDING,
            created_at=datetime.now(),
        )

        if request.has_trim and request.media_type != "video":
            raise ValueError("Trimming is only available for video and audio downloads")

        if request.compression is not None:
            if request.audio_only or request.media_type != "video":
                raise ValueError("Compression is only available for video downloads")
            if request.has_trim:
                # One range for both: the encoder cuts it exactly, nothing is trimmed afterwards
                request.compression = request.compression.model_copy(
                    update={"trim_start": request.trim_start, "trim_end": request.trim_end})
            media = await asyncio.get_running_loop().run_in_executor(
                None, ytdlp_wrapper.extract_info_cached, str(request.url))
            task.title = request.title or media.title
            compressor.admit(task, media, quality_cap(request.quality), request.compression,
                             client_id, client_ip)

        async with self._lock:
            self.tasks[task_id] = task
            if request.compression is None:
                self._cancel_events[task_id] = threading.Event()

        # Start background download
        asyncio.get_event_loop().run_in_executor(
            self.executor,
            self._process_download,
            task_id,
            request,
            client_id,
        )

        return task

    def _process_download(self, task_id: str, request: DownloadRequest, client_id: str):
        """Process download in background thread."""
        task = self.tasks.get(task_id)
        if not task:
            return

        # Create per-user download directory
        user_dir = settings.downloads_dir / client_id
        user_dir.mkdir(parents=True, exist_ok=True)
        work_dir = None
        cancel = self._cancel_events.get(task_id)
        partial_files: set[str] = set()

        try:
            # Update status to processing
            task.status = TaskStatus.PROCESSING

            # Use title from request if provided (e.g. carousel entries),
            # otherwise extract it (compression tasks already have it from admission)
            if request.title:
                task.title = request.title
            elif not task.title:
                try:
                    info = ytdlp_wrapper.extract_info(str(request.url))
                    task.title = info.title
                except Exception as e:
                    logger.warning(f"Could not extract title: {e}")

            # Update status to downloading
            task.status = TaskStatus.DOWNLOADING

            job = compressor.get(task_id)
            if job:
                self._download_for_compression(task, request, job)
                return

            # A clip is downloaded aside (removed by compressor.cleanup_orphans if
            # the server restarts meanwhile) and only the trimmed file reaches the user
            out_dir = user_dir
            if request.has_trim:
                work_dir = compressor.work_dir / task_id
                work_dir.mkdir(parents=True, exist_ok=True)
                out_dir = work_dir

            if cancel and cancel.is_set():
                raise DownloadCancelled()

            # Create progress hook
            def progress_hook(d):
                for key in ("tmpfilename", "filename"):
                    if d.get(key):
                        partial_files.add(d[key])
                if cancel and cancel.is_set():
                    raise DownloadCancelled()
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
            if request.media_type == "image":
                filepath = ytdlp_wrapper.download_image(
                    str(request.url),
                    out_dir,
                    source_url=request.source_url,
                    title=task.title,
                    progress_hook=progress_hook,
                )
            elif request.audio_only:
                filepath = ytdlp_wrapper.download_audio(
                    str(request.url),
                    out_dir,
                    progress_hook=progress_hook,
                )
            elif request.source_url and re.search(r'instagram\.com', str(request.url)):
                # Instagram video with direct CDN URL from instaloader
                filepath = ytdlp_wrapper.download_instagram_video(
                    str(request.url),
                    out_dir,
                    source_url=request.source_url,
                    title=task.title,
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
                    out_dir,
                    format_spec=format_spec,
                    progress_hook=progress_hook,
                )

            if cancel and cancel.is_set():
                partial_files.add(str(filepath))
                raise DownloadCancelled()

            if work_dir:
                task.status = TaskStatus.TRIMMING
                task.speed = task.eta = None
                filepath = deliver_trimmed(Path(filepath), work_dir, user_dir,
                                           request.trim_start, request.trim_end)

            # Update task with completion info
            task.status = TaskStatus.COMPLETED
            task.progress = 100
            task.filename = Path(filepath).name
            task.completed_at = datetime.now()

            logger.info(f"Task {task_id} completed: {task.filename}")

        except Exception as e:
            if cancel and cancel.is_set():
                logger.info(f"Task {task_id} cancelled")
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
                # Partial and already merged files in the user's directory
                for name in partial_files:
                    Path(name).unlink(missing_ok=True)
                    Path(name + ".part").unlink(missing_ok=True)
            else:
                logger.error(f"Task {task_id} failed: {e}")
                task.status = TaskStatus.FAILED
                task.error = str(e)
            compressor.release(task_id)  # no-op unless a compression job is still reserved
        finally:
            self._cancel_events.pop(task_id, None)
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _download_for_compression(self, task: DownloadTask, request: DownloadRequest, job):
        """Download into the job's work dir and hand the file to the compression queue."""
        max_bytes = settings.compression_max_input_file_mb * 1_000_000

        def progress_hook(d):
            if job.cancel.is_set():
                raise DownloadCancelled()
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if max(total, downloaded) > max_bytes:
                    raise ValueError(f"The source is larger than {settings.compression_max_input_file_mb} MB")
                if total > 0:
                    task.progress = (downloaded / total) * 100
                task.speed = d.get("_speed_str", "")
                task.eta = d.get("_eta_str", "")
            elif d["status"] == "finished":
                task.status = TaskStatus.MERGING
                task.progress = 100

        try:
            if request.source_url and re.search(r'instagram\.com', str(request.url)):
                filepath = ytdlp_wrapper.download_instagram_video(
                    str(request.url),
                    job.work_dir,
                    source_url=request.source_url,
                    title=task.title,
                    progress_hook=progress_hook,
                )
            else:
                # Exact formats chosen by the planner; the quality spec is only a fallback
                fallback = YTDLPWrapper.get_format_for_quality(request.quality or "best")
                spec = f"{job.source.format_spec}/{fallback}" if job.source.format_spec else fallback
                filepath = ytdlp_wrapper.download(
                    str(request.url),
                    job.work_dir,
                    format_spec=spec,
                    progress_hook=progress_hook,
                    convert_to_mp4=False,
                    max_filesize=max_bytes,
                )
            if not Path(filepath).exists():
                raise ValueError("Download produced no file (larger than the compression size limit?)")
        except Exception as e:
            compressor.release(task.task_id)
            if job.cancel.is_set():
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
                logger.info(f"Task {task.task_id} cancelled during download")
            else:
                logger.error(f"Task {task.task_id} failed: {e}")
                task.status = TaskStatus.FAILED
                task.error = str(e)
            return

        if job.cancel.is_set():
            compressor.release(task.task_id)
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            return
        compressor.submit(task.task_id, Path(filepath))

    async def cancel_task(self, task_id: str, client_id: str) -> bool:
        """Cancel an active task: a plain download, or a compression task
        (download, queue or encoding)."""
        task = await self.get_task(task_id, client_id)
        if not task or task.status in TERMINAL_STATUSES:
            return False
        cancel = self._cancel_events.get(task_id)
        if cancel:
            cancel.set()  # the download thread stops at its next progress update
            return True
        return compressor.cancel(task_id)

    async def get_task(self, task_id: str, client_id: str) -> Optional[DownloadTask]:
        """Get task by ID, scoped to client."""
        task = self.tasks.get(task_id)
        if task and task.client_id == client_id:
            return task
        return None

    async def get_all_tasks(self, client_id: str) -> list[DownloadTask]:
        """Get all tasks for a client."""
        return [t for t in self.tasks.values() if t.client_id == client_id]

    async def get_active_tasks(self, client_id: str) -> list[DownloadTask]:
        """Get active (non-completed) tasks for a client."""
        return [
            t for t in self.tasks.values()
            if t.client_id == client_id
            and t.status not in TERMINAL_STATUSES
        ]

    async def delete_task(self, task_id: str, client_id: str) -> bool:
        """Delete a task, scoped to client."""
        async with self._lock:
            task = self.tasks.get(task_id)
            if task and task.client_id == client_id:
                del self.tasks[task_id]
                # An invisible task must not keep encoding
                if task.status not in TERMINAL_STATUSES:
                    if task_id in self._cancel_events:
                        self._cancel_events[task_id].set()
                    else:
                        compressor.cancel(task_id)
                return True
        return False

    async def clear_completed(self, client_id: str):
        """Clear all completed tasks for a client."""
        async with self._lock:
            self.tasks = {
                k: v for k, v in self.tasks.items()
                if not (v.client_id == client_id
                        and v.status in TERMINAL_STATUSES)
            }


# Singleton instance
task_manager = TaskManager()
