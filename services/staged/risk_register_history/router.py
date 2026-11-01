"""HTTP surface for the risk register history service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import risk_register_history

router = APIRouter(prefix="/api/risk-register", tags=["risk-register"])


class RiskRegisterHistoryRow(BaseModel):
    server_id: str
    server_name: str | None = None
    risk_tier: str | None = None
    overall_risk: float | None = None
    verdict: str | None = None
    confidence: float | None = None
    computed_at: str


class RiskRegisterHistoryResponse(BaseModel):
    days: int
    rows: list[RiskRegisterHistoryRow]
    count: int


@router.get("/history", response_model=RiskRegisterHistoryResponse)
def get_risk_register_history(
    server_id: str | None = Query(default=None),
    days: int = Query(default=30),
    limit: int = Query(default=100),
    db: Session = Depends(get_session),
) -> RiskRegisterHistoryResponse:
    days = max(1, min(365, days))
    limit = max(1, min(1000, limit))

    result = risk_register_history(
        db,
        server_id=server_id,
        days=days,
        limit=limit,
    )

    if isinstance(result, dict):
        rows = result.get("rows", [])
        response_days = result.get("days", days)
        count = result.get("count", len(rows))
    else:
        rows = result
        response_days = days
        count = len(rows)

    normalized_rows = []
    for row in rows:
        if not isinstance(row, dict):
            row = {
                "server_id": getattr(row, "server_id", ""),
                "server_name": getattr(row, "server_name", None),
                "risk_tier": getattr(row, "risk_tier", None),
                "overall_risk": getattr(row, "overall_risk", None),
                "verdict": getattr(row, "verdict", None),
                "confidence": getattr(row, "confidence", None),
                "computed_at": getattr(row, "computed_at", None),
            }

        computed_at = row.get("computed_at")
        if computed_at is not None and not isinstance(computed_at, str):
            computed_at = computed_at.isoformat()

        normalized_rows.append(
            RiskRegisterHistoryRow(
                server_id=str(row.get("server_id", "")),
                server_name=row.get("server_name"),
                risk_tier=row.get("risk_tier"),
                overall_risk=row.get("overall_risk"),
                verdict=row.get("verdict"),
                confidence=row.get("confidence"),
                computed_at=computed_at,
            )
        )

    return RiskRegisterHistoryResponse(
        days=int(response_days),
        rows=normalized_rows,
        count=int(count),
    )