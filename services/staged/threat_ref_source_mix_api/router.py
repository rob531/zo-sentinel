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

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import threat_ref_source_mix

router = APIRouter(prefix="/api", tags=["threat"])


class SourceMixItem(BaseModel):
    source: str
    count: int
    aggregator_count: int
    latest_fetched_at: Optional[datetime]


class IndicatorTypeItem(BaseModel):
    indicator_type: str
    count: int


class ThreatRefSourceMixResponse(BaseModel):
    generated_at: datetime
    total: int
    by_source: List[SourceMixItem]
    by_indicator_type: List[IndicatorTypeItem]


@router.get("/threat/ref-source-mix", response_model=ThreatRefSourceMixResponse)
def get_ref_source_mix(db: Session = Depends(get_session)) -> ThreatRefSourceMixResponse:
    data = threat_ref_source_mix(db)
    return ThreatRefSourceMixResponse(**data)