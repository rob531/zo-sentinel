"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_indicator_catalog

router = APIRouter(prefix="/api", tags=["threat_intel"])


class ByTypeItem(BaseModel):
    indicator_type: str
    count: int


class BySourceItem(BaseModel):
    source: str
    count: int


class PulseItem(BaseModel):
    pulse_id: int
    pulse_name: Optional[str] = None
    indicator_count: int
    newest_fetched_at: datetime


class RecentItem(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str
    fetched_at: datetime


class IndicatorCatalogResponse(BaseModel):
    total: int
    by_type: List[ByTypeItem]
    by_source: List[BySourceItem]
    aggregator_count: int
    pulses: List[PulseItem]
    recent: List[RecentItem]


@router.get(
    "/threat-intel/indicators",
    response_model=IndicatorCatalogResponse,
    summary="Retrieve threat‑intel indicator catalog",
)
def get_indicators(
    source: Optional[str] = Query(
        default=None, description="Filter results by source name"
    ),
    indicator_type: Optional[str] = Query(
        default=None, description="Filter results by indicator type"
    ),
    db: Session = Depends(get_session),
) -> IndicatorCatalogResponse:
    """
    Return aggregated threat‑intel indicator data.

    Optional query parameters `source` and `indicator_type` are applied as
    filters in the underlying SQL query (parameterised, never interpolated).
    """
    data = get_indicator_catalog(db, source=source, indicator_type=indicator_type)
    return IndicatorCatalogResponse(**data)