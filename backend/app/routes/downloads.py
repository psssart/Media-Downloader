from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from typing import List
import urllib.request
import logging

from ..models import (
    URLRequest,
    MediaInfo,
    DownloadRequest,
    DownloadTask,
    DownloadedFile,
    CompressionEstimateRequest,
)
from ..dependencies import get_client_id, get_client_ip
from ..services.ytdlp_wrapper import ytdlp_wrapper
from ..services.task_manager import task_manager
from ..services.compressor import compressor, quality_cap, AdmissionRejected
from ..services.file_manager import file_manager

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.post("/extract", response_model=MediaInfo)
async def extract_info(request: URLRequest):
    """Extract media information from URL."""
    try:
        # In a thread: yt-dlp is blocking and must not stall the event loop
        info = await run_in_threadpool(ytdlp_wrapper.extract_info_cached, str(request.url), True)
        return info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")


@router.post("/start", response_model=DownloadTask)
async def start_download(
    request: DownloadRequest,
    client_id: str = Depends(get_client_id),
    client_ip: str | None = Depends(get_client_ip),
):
    """Start a new download task.

    Compression requests that don't pass admission get 409 with a structured
    detail: {code, message, ..., alternatives: [{kind, label, options}]}.
    """
    try:
        task = await task_manager.create_task(request, client_id, client_ip)
        return task
    except AdmissionRejected as e:
        return JSONResponse(status_code=409, content={"detail": e.detail})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start download: {str(e)}")


@router.get("/compression/presets")
async def get_compression_presets():
    """Platform presets and limits for the compression UI."""
    return {
        "enabled": settings.compression_enabled,
        "presets": compressor.public_presets(),
        # Output resolutions (short side) a user may pick instead of auto
        "resolutions": sorted((h for h in settings.compression_resolution_ladder
                               if h <= settings.compression_max_input_height), reverse=True),
        "max_total_wait_seconds": settings.compression_max_total_wait_sec,
        "max_input_duration_seconds": settings.compression_max_input_duration_sec,
    }


@router.post("/compression/estimate")
async def estimate_compression(
    request: CompressionEstimateRequest,
    client_id: str = Depends(get_client_id),
    client_ip: str | None = Depends(get_client_ip),
):
    """Admission dry run from yt-dlp metadata: verdict, plan and alternatives."""
    try:
        media = await run_in_threadpool(ytdlp_wrapper.extract_info_cached, str(request.url))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return compressor.estimate(media, quality_cap(request.quality), request.compression,
                               client_id, client_ip)


@router.get("/compression/stats")
async def get_compression_stats():
    """Estimate-vs-actual metrics for tuning decode factors and the safety factor."""
    return compressor.stats()


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, client_id: str = Depends(get_client_id)):
    """Cancel an active download or compression task."""
    if not await task_manager.cancel_task(task_id, client_id):
        raise HTTPException(status_code=404, detail="No active task with this ID")
    return {"message": "Task cancelled"}


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
