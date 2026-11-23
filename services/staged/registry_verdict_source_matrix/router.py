"""HTTP surface for the registry verdict-source matrix service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_verdict_source_matrix

router = APIRouter(prefix="/api", tags=["registry"])


class TierMatrixEntry(BaseModel):
    tier: str
    count: int
    pct: float


class SourceMatrixEntry(BaseModel):
    source: str
    tiers: list[TierMatrixEntry]
    total: int


class VerdictSourceMatrixResponse(BaseModel):
    sources: list[SourceMatrixEntry]
    generated_at: datetime


@router.get(
    "/registry/verdict-source-matrix",
    response_model=VerdictSourceMatrixResponse,
)
def get_registry_verdict_source_matrix(
    db: Session = Depends(get_session),
    source: str | None = Query(default=None),
    limit: int = Query(default=50),
) -> VerdictSourceMatrixResponse:
    return get_verdict_source_matrix(db, source=source, limit=limit)