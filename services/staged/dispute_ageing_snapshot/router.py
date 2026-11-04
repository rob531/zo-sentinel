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

from .logic import dispute_ageing_snapshot

router = APIRouter(prefix="/api/dispute", tags=["dispute"])


class AgeRange(BaseModel):
    range: str
    count: int


class AgeingSnapshotResponse(BaseModel):
    age_ranges: list[AgeRange]


@router.get("/ageing", response_model=AgeingSnapshotResponse)
def get_ageing_snapshot(db: Session = Depends(get_session)) -> AgeingSnapshotResponse:
    age_ranges = dispute_ageing_snapshot(db)
    return AgeingSnapshotResponse(age_ranges=age_ranges)