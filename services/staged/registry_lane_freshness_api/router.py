from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_lane_freshness

router = APIRouter(prefix="/api", tags=["registry"])


class RegistryLane(BaseModel):
    registry_source: str
    server_count: int
    scanned_count: int
    unscanned_count: int
    stale_count: int
    tier_counts: dict[str, int]
    median_scan_count: float
    oldest_first_seen: datetime | None = None
    newest_last_seen: datetime | None = None


class RegistryLaneTotals(BaseModel):
    servers: int
    scanned: int
    stale: int


class RegistryLaneFreshnessResponse(BaseModel):
    generated_at: datetime
    lanes: list[RegistryLane]
    totals: RegistryLaneTotals


@router.get(
    "/registry/lanes",
    response_model=RegistryLaneFreshnessResponse,
)
def get_registry_lanes(db: Session = Depends(get_session)) -> RegistryLaneFreshnessResponse:
    payload = registry_lane_freshness(db)
    return RegistryLaneFreshnessResponse(**payload)