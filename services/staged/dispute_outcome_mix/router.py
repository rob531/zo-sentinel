"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# The logic function that computes the outcome mix.
from .logic import dispute_outcome_mix

router = APIRouter(prefix="/api", tags=["dispute_outcome_mix"])


class OutcomeMixResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason_category: Dict[str, int]
    by_proposed_tier: Dict[int, int]
    resolved_count: int
    open_count: int
    resolution_rate: float
    median_resolution_hours: Optional[float]


@router.get("/disputes/outcome-mix", response_model=OutcomeMixResponse)
def get_outcome_mix(db: Session = Depends(get_session)) -> OutcomeMixResponse:
    """Return a summary of dispute outcomes."""
    result = dispute_outcome_mix(db)
    return OutcomeMixResponse(**result)