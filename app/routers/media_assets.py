"""Public marketing/media assets -- /static/media/<file>.

Chairman-built 2026-07-17: hosts the 200K-campaign robot ad (MP4/GIF/still)
so social posts can use an on-brand mcprisky.io URL instead of file uploads.
Public by design -- same visibility tier as the landing page; nothing keyed,
nothing data-bearing, so THE LINE (freshness gating) does not apply.

Allowlist-only: serves exactly what ships in app/static/media with a fixed
extension->content-type map. No directory listing, no traversal surface.
"""
import pathlib

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_MEDIA = pathlib.Path(__file__).resolve().parents[1] / "static" / "media"
_TYPES = {".mp4": "video/mp4", ".gif": "image/gif", ".png": "image/png"}


@router.get("/static/media/{filename}")
def media_asset(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=404)
    path = _MEDIA / filename
    if not path.is_file() or path.suffix.lower() not in _TYPES:
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type=_TYPES[path.suffix.lower()],
        headers={"Cache-Control": "public, max-age=86400"},
    )