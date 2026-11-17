"""HTTP surface for registry last-assessed staleness."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_staleness_buckets

router = APIRouter(
    prefix="/api",
    tags=["registry-last-assessed-staleness"],
)


class StalenessBucketResponse(BaseModel):
    bucket: str
    count: int
    sample_server_ids: list[str]


class StalenessResponse(BaseModel):
    max_age_hours: int
    buckets: list[StalenessBucketResponse]


@router.get(
    "/registry/last-assessed-staleness",
    response_model=StalenessResponse,
)
def get_last_assessed_staleness(
    max_age_hours: int = Query(..., ge=1),
    db: Session = Depends(get_session),
) -> StalenessResponse:
    buckets = get_staleness_buckets(db, max_age_hours)
    return StalenessResponse(
        max_age_hours=max_age_hours,
        buckets=buckets,
    )