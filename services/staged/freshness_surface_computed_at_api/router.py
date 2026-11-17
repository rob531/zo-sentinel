from datetime import datetime, timezone
import inspect

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import get_session
from app.models import FreshnessStat

from .logic import latest_by_surface


router = APIRouter(prefix="/api")


class FreshnessSurfaceEntry(BaseModel):
    surface: str
    computed_at: datetime | None = None
    age_seconds: float | None = None
    compute_seconds: float
    stale: bool
    payload_keys: list[str] = Field(default_factory=list)


class FreshnessSurfaceResponse(BaseModel):
    as_of: datetime
    surfaces: list[FreshnessSurfaceEntry]
    count: int


@router.get(
    "/freshness/surface-computed-at",
    response_model=FreshnessSurfaceResponse,
)
async def surface_computed_at(session=Depends(get_session)) -> FreshnessSurfaceResponse:
    now = datetime.now(timezone.utc)
    statement = select(
        FreshnessStat.surface,
        FreshnessStat.payload,
        FreshnessStat.computed_at,
        FreshnessStat.compute_seconds,
    )

    result = session.execute(statement)
    if inspect.isawaitable(result):
        result = await result

    rows = result.all()
    if inspect.isawaitable(rows):
        rows = await rows

    surfaces = latest_by_surface(list(rows), now)
    return FreshnessSurfaceResponse(
        as_of=now,
        surfaces=surfaces,
        count=len(surfaces),
    )


if __name__ == "__main__":
    print("PASS")