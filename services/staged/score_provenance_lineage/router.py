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

from .logic import score_provenance_lineage

router = APIRouter(prefix="/api", tags=["score_provenance_lineage"])


class ModelGroupResponse(BaseModel):
    model_version: str | None
    adapter_sha256: str | None
    decision_rule_version: str | None
    axis_count: int
    last_scored_at: str | None
    max_p_top: float


class ProvenanceResponse(BaseModel):
    server_id: str
    risk_tier: str | None
    stale: bool
    stale_days: int
    models: list[ModelGroupResponse]


@router.get("/scores/provenance", response_model=ProvenanceResponse)
def get_score_provenance(
    server_id: str = Query(..., min_length=1),
    stale_days: int = Query(30, ge=0),
    db: Session = Depends(get_session),
) -> ProvenanceResponse:
    result = score_provenance_lineage(
        db,
        server_id=server_id,
        stale_days=stale_days,
    )
    return ProvenanceResponse(**result)