from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import scan_age_histogram

router = APIRouter(prefix="/api", tags=["registry"])


class ScanAgeBucket(BaseModel):
    label: str
    min_days: float | None
    max_days: float | None
    count: int
    tiers: dict[str, int]


class ScanAgeHistogramResponse(BaseModel):
    buckets: list[ScanAgeBucket]
    never_scanned: int
    total: int


@router.get(
    "/registry/scan-age-histogram",
    response_model=ScanAgeHistogramResponse,
)
def get_scan_age_histogram(
    db: Session = Depends(get_session),
) -> ScanAgeHistogramResponse:
    return ScanAgeHistogramResponse.model_validate(scan_age_histogram(db))