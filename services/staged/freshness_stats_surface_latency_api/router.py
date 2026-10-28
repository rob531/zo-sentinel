from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_freshness_stats

router = APIRouter(prefix="/api")


class FreshnessSurfaceStats(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    age_seconds: float
    payload_keys: list[str]


class FreshnessStatsResponse(BaseModel):
    surfaces: list[FreshnessSurfaceStats]
    count: int


@router.get("/freshness/surfaces", response_model=FreshnessStatsResponse)
def freshness_surfaces(session: Session = Depends(get_session)) -> FreshnessStatsResponse:
    surfaces = get_freshness_stats(session)
    return FreshnessStatsResponse(surfaces=surfaces, count=len(surfaces))