"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import reconcile_disputes

router = APIRouter(prefix="/api", tags=["disputes"])


class ReconciliationItem(BaseModel):
    dispute_id: int
    server_id: str
    server_name: str | None = None
    status: str
    reason_category: str
    proposed_overall_risk: Any
    current_risk_tier: Any
    tier_delta: Any
    mismatched_axes: list[str] = []
    created_at: Any = None
    resolved_at: Any = None


class ReconciliationResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    items: list[ReconciliationItem]


@router.get(
    "/disputes/reconciliation",
    response_model=ReconciliationResponse,
)
def get_dispute_reconciliation(
    db: Session = Depends(get_session),
) -> ReconciliationResponse:
    result = reconcile_disputes(db)
    return ReconciliationResponse(**result)