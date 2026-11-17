"""HTTP surface for MCP attestation coverage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_attestation_coverage

router = APIRouter(prefix="/api", tags=["attestations"])


class CoveragePoint(BaseModel):
    date: str
    servers_total: int
    servers_attested: int
    coverage_pct: float


class CoverageResponse(BaseModel):
    days: int
    series: list[CoveragePoint]


@router.get("/attestations/coverage", response_model=CoverageResponse)
def get_coverage(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    return get_attestation_coverage(db, days)