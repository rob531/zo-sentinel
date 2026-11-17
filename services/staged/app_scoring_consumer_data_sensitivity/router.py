"""Read-only HTTP surface for the data-sensitivity scoring-consumer view."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_data_sensitivity_scores

router = APIRouter(
    prefix="/api/scoring",
    tags=["scoring"],
)

AxisTier = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class ServerDataSensitivity(BaseModel):
    server_id: str
    name: str | None = None
    url: str | None = None
    axis_score: float
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: str | None = None
    axis_tier: AxisTier
    registry_risk_tier: str | None = None
    scored_at: datetime | None = None
    decision_rule_version: str | None = None
    model_version: str | None = None


class DataSensitivityResponse(BaseModel):
    axis: Literal["data_sensitivity"]
    generated_at: datetime
    tier_counts: dict[str, int]
    servers: list[ServerDataSensitivity]


@router.get(
    "/data-sensitivity",
    response_model=DataSensitivityResponse,
    summary="Get the data-sensitivity scoring-consumer view",
)
def get_data_sensitivity(
    limit: int = Query(default=50, ge=1, le=500),
    tier: AxisTier | None = Query(default=None),
    db: Session = Depends(get_session),
) -> DataSensitivityResponse:
    """Return the newest data-sensitivity score for each registered server."""
    result = get_data_sensitivity_scores(db, limit=limit, tier=tier)

    if isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {
            "axis": "data_sensitivity",
            "generated_at": datetime.now(timezone.utc),
            "tier_counts": {},
            "servers": result,
        }

    payload.setdefault("axis", "data_sensitivity")
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    payload.setdefault("tier_counts", {})
    payload.setdefault("servers", [])
    return DataSensitivityResponse.model_validate(payload)