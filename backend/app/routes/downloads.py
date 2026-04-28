from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import FileResponse, Response
from typing import List
import urllib.request
import logging

from ..models import (
    URLRequest,
    MediaInfo,
    DownloadRequest,
    DownloadTask,
    DownloadedFile,
)
from ..dependencies import get_client_id
from ..services.ytdlp_wrapper import ytdlp_wrapper
from ..services.task_manager import task_manager
from ..services.file_manager import file_manager

logger = logging.getLogger(__name__)

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
async def start_download(request: DownloadRequest, client_id: str = Depends(get_client_id)):
    """Start a new download task."""
    try:
        task = await task_manager.create_task(request, client_id)
        return task
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start download: {str(e)}")


@router.get("/tasks", response_model=List[DownloadTask])
async def get_tasks(client_id: str = Depends(get_client_id)):
    """Get all download tasks for the current client."""
    return await task_manager.get_all_tasks(client_id)


@router.get("/tasks/{task_id}", response_model=DownloadTask)
async def get_task(task_id: str, client_id: str = Depends(get_client_id)):
    """Get a specific task by ID."""
    task = await task_manager.get_task(task_id, client_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, client_id: str = Depends(get_client_id)):
    """Delete a task."""
    success = await task_manager.delete_task(task_id, client_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Task deleted"}


@router.delete("/tasks")
async def clear_completed_tasks(client_id: str = Depends(get_client_id)):
    """Clear all completed tasks for the current client."""
    await task_manager.clear_completed(client_id)
    return {"message": "Completed tasks cleared"}


@router.get("/files", response_model=List[DownloadedFile])
async def get_files(client_id: str = Depends(get_client_id)):
    """Get list of downloaded files for the current client."""
    return file_manager.get_files(client_id)


@router.get("/proxy-thumbnail")
async def proxy_thumbnail(url: str = Query(..., description="External thumbnail URL")):
    """Proxy an external thumbnail image to bypass referrer restrictions."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            data = resp.read(5 * 1024 * 1024)  # 5MB limit

        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        logger.warning(f"Failed to proxy thumbnail: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch thumbnail")


@router.get("/thumbnails/{filename}")
async def get_thumbnail(filename: str, client_id: str = Depends(get_client_id)):
    """Serve a thumbnail image."""
    filepath = file_manager.get_thumbnail_path(filename, client_id)
    if not filepath:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    import mimetypes
    media_type = mimetypes.guess_type(str(filepath))[0] or "image/jpeg"

    return FileResponse(
        path=filepath,
        media_type=media_type,
    )


@router.get("/files/{filename}")
async def download_file(filename: str, client_id: str = Depends(get_client_id)):
    """Download a file."""
    filepath = file_manager.get_file_path(filename, client_id)
    if not filepath:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/files/{filename}")
async def delete_file(filename: str, client_id: str = Depends(get_client_id)):
    """Delete a file."""
    success = file_manager.delete_file(filename, client_id)
    if not success:
        raise HTTPException(status_code=404, detail="File not found")
    return {"message": "File deleted"}


@router.get("/stats")
async def get_stats(client_id: str = Depends(get_client_id)):
    """Get storage statistics for the current client."""
    return file_manager.get_storage_stats(client_id)
