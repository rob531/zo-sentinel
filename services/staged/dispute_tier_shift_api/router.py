"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import dispute_tier_shift, DisputeTierShiftResponse

router = APIRouter(prefix="/api", tags=["dispute_tier_shift"])


@router.get("/disputes/tier-shift", response_model=DisputeTierShiftResponse)
def get_dispute_tier_shift(db: Session = Depends(get_session)) -> DisputeTierShiftResponse:
    """Return aggregated dispute tier‑shift statistics."""
    result = dispute_tier_shift(db)
    return DisputeTierShiftResponse(**result)