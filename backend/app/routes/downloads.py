from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from typing import List

from ..models import (
    URLRequest,
    MediaInfo,
    DownloadRequest,
    DownloadTask,
    DownloadedFile,
)
from ..services.ytdlp_wrapper import ytdlp_wrapper
from ..services.task_manager import task_manager
from ..services.file_manager import file_manager

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.post("/extract", response_model=MediaInfo)
async def extract_info(request: URLRequest):
    """Extract media information from URL."""
    try:
        info = ytdlp_wrapper.extract_info(str(request.url))
        return info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")


@router.post("/start", response_model=DownloadTask)
async def start_download(request: DownloadRequest):
    """Start a new download task."""
    try:
        task = await task_manager.create_task(request)
        return task
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start download: {str(e)}")


@router.get("/tasks", response_model=List[DownloadTask])
async def get_tasks():
    """Get all download tasks."""
    return await task_manager.get_all_tasks()


@router.get("/tasks/{task_id}", response_model=DownloadTask)
async def get_task(task_id: str):
    """Get a specific task by ID."""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task."""
    success = await task_manager.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Task deleted"}


@router.delete("/tasks")
async def clear_completed_tasks():
    """Clear all completed tasks."""
    await task_manager.clear_completed()
    return {"message": "Completed tasks cleared"}


@router.get("/files", response_model=List[DownloadedFile])
async def get_files():
    """Get list of downloaded files."""
    return file_manager.get_files()


@router.get("/files/{filename}")
async def download_file(filename: str):
    """Download a file."""
    filepath = file_manager.get_file_path(filename)
    if not filepath:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/files/{filename}")
async def delete_file(filename: str):
    """Delete a file."""
    success = file_manager.delete_file(filename)
    if not success:
        raise HTTPException(status_code=404, detail="File not found")
    return {"message": "File deleted"}


@router.get("/stats")
async def get_stats():
    """Get storage statistics."""
    return file_manager.get_storage_stats()
