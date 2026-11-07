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
from typing import List

from app.db import get_session

from .logic import axis_tier_matrix

router = APIRouter(prefix="/api/scoring", tags=["axis_tier_matrix"])


class AxisInfo(BaseModel):
    axis_name: str
    label: str


class CellInfo(BaseModel):
    axis_name: str
    risk_tier: str
    count: int
    mean_p_top: float | None


class AxisTierMatrixResponse(BaseModel):
    axes: List[AxisInfo]
    tiers: List[str]
    cells: List[CellInfo]
    generated_at: str


@router.get("/axis-tier-matrix", response_model=AxisTierMatrixResponse)
def get_axis_tier_matrix(db: Session = Depends(get_session)) -> AxisTierMatrixResponse:
    result = axis_tier_matrix(db)
    return AxisTierMatrixResponse(**result)