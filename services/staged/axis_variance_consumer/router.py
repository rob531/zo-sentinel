from fastapi import APIRouter, Depends, Query
from typing import List, Optional

from app.db import get_session
from .logic import AxisVarianceResponse, get_axis_variance

router = APIRouter(prefix="/api/scoring", tags=["axis-variance"])


@router.get(
    "/axis-variance",
    response_model=AxisVarianceResponse,
    summary="Compute per‑axis variance for servers",
)
def axis_variance(
    server_ids: Optional[str] = Query(
        default=None,
        description="Comma‑separated list of server IDs to include; omit for all servers in the window",
    ),
    window_days: int = Query(
        default=7,
        ge=1,
        description="Number of days back to include in the variance calculation",
    ),
    db=Depends(get_session),
):
    """
    Return per‑axis mean, standard deviation, sample count and trend for the
    requested servers over the specified scoring window.
    """
    ids: Optional[List[str]] = (
        [sid.strip() for sid in server_ids.split(",")] if server_ids else None
    )
    return get_axis_variance(db, ids, window_days)