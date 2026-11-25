"""HTTP surface for querying verdict-reasoning text audit records."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import query_reasoning_audit

router = APIRouter(prefix="/api", tags=["verdict-reasoning-text-audit"])


class TierAuditSummary(BaseModel):
    total: int
    missing: int
    avg_reasoning_chars: float


class ReasoningAuditItem(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    verdict: str | None
    risk_tier: str | None
    confidence: float | None
    reasoning_chars: int
    has_reasoning: bool
    last_assessed: datetime | None


class ReasoningAuditResponse(BaseModel):
    total: int
    missing_reasoning: int
    by_tier: dict[str, TierAuditSummary]
    items: list[ReasoningAuditItem]


@router.get("/verdict/reasoning-audit", response_model=ReasoningAuditResponse)
def get_reasoning_audit(
    db: Session = Depends(get_session),
    risk_tier: str | None = Query(default=None),
    registry_source: str | None = Query(default=None),
    missing_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ReasoningAuditResponse:
    result = query_reasoning_audit(
        db,
        risk_tier=risk_tier,
        registry_source=registry_source,
        missing_only=missing_only,
        limit=limit,
        offset=offset,
    )
    return ReasoningAuditResponse(**result)