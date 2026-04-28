import re

from fastapi import Header, Query, HTTPException


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def get_client_id(
    x_client_id: str | None = Header(None),
    client_id: str | None = Query(None),
) -> str:
    """Extract and validate client_id from header or query param.

    Header takes priority. Query param exists as fallback for direct URL
    access (file downloads, thumbnails in <img>/<a> tags).
    """
    cid = x_client_id or client_id
    if not cid or not UUID_PATTERN.match(cid):
        raise HTTPException(
            status_code=400,
            detail="Valid X-Client-ID header (UUID format) is required",
        )
    return cid.lower()
