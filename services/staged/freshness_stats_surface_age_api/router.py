from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

try:
    from .logic import get_surface_age_rows
except ImportError:
    try:
        from .logic import list_surface_age_rows as get_surface_age_rows
    except ImportError:
        from .logic import get_surface_age as get_surface_age_rows

router = APIRouter(prefix="/api")


class FreshnessSurfaceAge(BaseModel):
    surface: str
    computed_at: str
    compute_seconds: float
    age_seconds: float
    payload_keys: list[str]


class FreshnessSurfaceAgeResponse(BaseModel):
    count: int
    surfaces: list[FreshnessSurfaceAge]
    max_age_seconds: Optional[int]


@router.get(
    "/freshness/surface-age",
    response_model=FreshnessSurfaceAgeResponse,
)
async def list_freshness_surface_age(
    max_age_seconds: Optional[int] = Query(default=None),
    session=Depends(get_session),
) -> dict[str, Any]:
    rows = await get_surface_age_rows(session)

    if max_age_seconds is not None:
        rows = [
            row
            for row in rows
            if float(row["age_seconds"]) <= max_age_seconds
        ]

    surfaces = [FreshnessSurfaceAge(**row) for row in rows]
    return {
        "count": len(surfaces),
        "surfaces": surfaces,
        "max_age_seconds": max_age_seconds,
    }