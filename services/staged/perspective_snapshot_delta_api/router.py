from fastapi import APIRouter, Depends, Query
from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

from .contract import PerspectiveSnapshotDeltaResponse

try:
    from .logic import get_perspective_snapshot_delta as _get_snapshot_delta
except ImportError:
    from .logic import get_snapshot_delta as _get_snapshot_delta

import inspect


router = APIRouter(
    prefix="/perspectives/snapshots",
    tags=["perspectives"],
)


@router.get("/delta", response_model=PerspectiveSnapshotDeltaResponse)
async def perspective_snapshot_delta(
    days: int = Query(default=7, ge=1, le=365),
    session=Depends(get_session),
):
    result = _get_snapshot_delta(session=session, days=days)
    if inspect.isawaitable(result):
        result = await result
    return result