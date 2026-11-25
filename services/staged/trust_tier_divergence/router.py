"""HTTP surface for trust-tier divergence reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import find_trust_tier_divergences

router = APIRouter(prefix="/api", tags=["trust-tier-divergence"])


class TrustTierDivergence(BaseModel):
    server_id: str
    name: str | None = None
    trust_score: float | None = None
    p_top: float | None = None
    registry_risk_tier: str | None = None
    expected_band: str
    delta_points: float
    scored_at: datetime | None = None
    decision_rule_version: str | None = None


class TrustTierDivergenceResponse(BaseModel):
    checked: int
    divergent: int
    divergences: list[TrustTierDivergence]


def _response_from_result(result: Any) -> TrustTierDivergenceResponse:
    if isinstance(result, dict):
        divergences = list(result.get("divergences", []))
        checked = int(result.get("checked", len(divergences)))
        divergent = int(result.get("divergent", len(divergences)))
    elif isinstance(result, tuple) and len(result) == 2:
        checked = int(result[0])
        divergences = list(result[1])
        divergent = len(divergences)
    else:
        divergences = list(result)
        checked = len(divergences)
        divergent = len(divergences)

    return TrustTierDivergenceResponse(
        checked=checked,
        divergent=divergent,
        divergences=divergences,
    )


@router.get(
    "/scores/trust-tier-divergence",
    response_model=TrustTierDivergenceResponse,
)
def get_trust_tier_divergence(
    db: Session = Depends(get_session),
    min_delta: float = Query(default=40.0, ge=0.0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> TrustTierDivergenceResponse:
    result = find_trust_tier_divergences(
        db,
        min_delta=min_delta,
        limit=limit,
    )
    return _response_from_result(result)


__all__ = [
    "McpLlmAxisScore",
    "McpServerRegistry",
    "TrustTierDivergence",
    "TrustTierDivergenceResponse",
    "router",
]