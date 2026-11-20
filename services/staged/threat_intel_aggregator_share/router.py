"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import aggregator_share

router = APIRouter(prefix="/api", tags=["threat-intel"])


class SourceShare(BaseModel):
    source: str
    count: int
    is_aggregator: bool


class IndicatorTypeShare(BaseModel):
    indicator_type: str
    count: int


class AggregatorShareResponse(BaseModel):
    days: int
    total: int
    by_aggregator: dict[str, int]
    by_source: list[SourceShare]
    by_indicator_type: list[IndicatorTypeShare]


@router.get(
    "/threat-intel/aggregator-share",
    response_model=AggregatorShareResponse,
)
def get_aggregator_share(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> AggregatorShareResponse:
    return AggregatorShareResponse(**aggregator_share(db, days))