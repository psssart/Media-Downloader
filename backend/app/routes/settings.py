from fastapi import APIRouter, HTTPException, UploadFile, File
from pathlib import Path

from ..config import settings
from ..models import CookieUpload, SettingsResponse
from ..services.file_manager import file_manager

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_cookies_path() -> Path:
    return settings.cookies_dir / "cookies.txt"


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Get current settings."""
    cookies_path = get_cookies_path()

    return SettingsResponse(
        cookies_configured=cookies_path.exists(),
        file_retention_hours=settings.file_retention_hours,
        max_concurrent_downloads=settings.max_concurrent_downloads,
    )


@router.post("/cookies/upload")
async def upload_cookies(file: UploadFile = File(...)):
    """Upload cookies.txt file."""
    try:
        content = await file.read()
        cookies_path = get_cookies_path()

        # Validate content is text
        try:
            content_str = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Invalid file format. Must be text.")

        # Basic validation for Netscape cookie format
        if not _validate_cookies_content(content_str):
            raise HTTPException(
                status_code=400,
                detail="Invalid cookies format. Must be Netscape cookie format."
            )

        # Save file
        cookies_path.write_text(content_str)

        return {"message": "Cookies uploaded successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save cookies: {str(e)}")


@router.post("/cookies/paste")
async def paste_cookies(data: CookieUpload):
    """Save pasted cookies content."""
    try:
        if not _validate_cookies_content(data.content):
            raise HTTPException(
                status_code=400,
                detail="Invalid cookies format. Must be Netscape cookie format."
            )

        cookies_path = get_cookies_path()
        cookies_path.write_text(data.content)

        return {"message": "Cookies saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save cookies: {str(e)}")


@router.delete("/cookies")
async def delete_cookies():
    """Delete cookies file."""
    cookies_path = get_cookies_path()

    if cookies_path.exists():
        cookies_path.unlink()
        return {"message": "Cookies deleted"}

    raise HTTPException(status_code=404, detail="No cookies file found")


@router.get("/cookies/status")
async def cookies_status():
    """Check if cookies are configured."""
    cookies_path = get_cookies_path()

    if cookies_path.exists():
        stat = cookies_path.stat()
        return {
            "configured": True,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        }

    return {"configured": False}


@router.post("/cleanup")
async def trigger_cleanup():
    """Manually trigger file cleanup."""
    deleted = file_manager.cleanup_old_files()
    return {"message": f"Cleanup completed. {deleted} files removed."}


def _validate_cookies_content(content: str) -> bool:
    """Basic validation for Netscape cookie format."""
    lines = content.strip().split("\n")

    # Check for header comment or valid entries
    has_valid_content = False

    for line in lines:
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            if "Netscape" in line or "HTTP Cookie" in line:
                has_valid_content = True
            continue

        # Cookie lines should have tab-separated fields
        parts = line.split("\t")
        if len(parts) >= 6:
            has_valid_content = True

    return has_valid_content
