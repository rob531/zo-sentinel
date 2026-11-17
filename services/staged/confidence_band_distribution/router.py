"""router.py -- confidence band distribution HTTP surface."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import confidence_band_distribution

router = APIRouter(prefix="/api", tags=["confidence-band-distribution"])


class ConfidenceBand(BaseModel):
    band: str
    count: int
    pct: float


class RegistrySourceSummary(BaseModel):
    registry_source: str
    count: int
    low_confidence_count: int


class ConfidenceBandDistributionResponse(BaseModel):
    total: int
    bands: list[ConfidenceBand]
    by_source: list[RegistrySourceSummary]


@router.get(
    "/registry/confidence-bands",
    response_model=ConfidenceBandDistributionResponse,
)
def get_confidence_band_distribution(
    registry_source: str | None = None,
    db: Session = Depends(get_session),
) -> ConfidenceBandDistributionResponse:
    payload = confidence_band_distribution(db, registry_source=registry_source)
    return ConfidenceBandDistributionResponse(**payload)