"""HTTP surface for the risk tier comparison service."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compare_risk_tiers as compare_risk_tiers_logic

router = APIRouter(prefix="/api", tags=["risk"])


class AxisScoreResponse(BaseModel):
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None


class ServerComparisonResponse(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    axes: dict[str, AxisScoreResponse]
    overall: Any = None
    criteria_version: str | None = None


class AxisComparisonResponse(BaseModel):
    a_label: str | None = None
    b_label: str | None = None
    delta: float | None = None


class RiskTierComparisonResponse(BaseModel):
    servers: list[ServerComparisonResponse]
    comparison: dict[str, AxisComparisonResponse]


@router.get("/risk/compare", response_model=RiskTierComparisonResponse)
def compare_risk_tiers(
    server_ids: str = Query(..., description="Comma-separated server IDs to compare"),
    db: Session = Depends(get_session),
) -> RiskTierComparisonResponse:
    ids = [server_id.strip() for server_id in server_ids.split(",") if server_id.strip()]
    return RiskTierComparisonResponse(**compare_risk_tiers_logic(db, ids))