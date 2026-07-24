"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import risk_tier_histogram

router = APIRouter(prefix="/api/example", tags=["example"])


class HistogramResponse(BaseModel):
    total: int
    histogram: dict[str, int]


@router.get("/histogram", response_model=HistogramResponse)
def get_histogram(db: Session = Depends(get_session)) -> HistogramResponse:
    hist = risk_tier_histogram(db)
    return HistogramResponse(total=sum(hist.values()), histogram=hist)
