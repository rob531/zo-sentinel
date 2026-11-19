"""HTTP surface for the verdict/risk-tier conflict service."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import list_verdict_tier_conflicts as _list_verdict_tier_conflicts
except ImportError:
    try:
        from .logic import find_verdict_tier_conflicts as _list_verdict_tier_conflicts
    except ImportError:
        try:
            from .logic import find_conflicts as _list_verdict_tier_conflicts
        except ImportError:
            from .logic import get_conflicts as _list_verdict_tier_conflicts


router = APIRouter(prefix="/api", tags=["verdicts"])


class ConflictItem(BaseModel):
    server_id: str
    server_name: str | None
    registry_verdict: str | None
    registry_risk_tier: str | None
    registry_trust_score: float | None
    registry_confidence: float | None
    scored_tier: str
    p_top: float
    p_critical: float | None
    p_danger: float | None
    model_version: str | None
    decision_rule_version: str | None
    scored_at: str | None
    conflict_kind: str


class ConflictResponse(BaseModel):
    total: int
    items: list[ConflictItem]


def _item_value(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


@router.get("/verdicts/conflicts", response_model=ConflictResponse)
def list_conflicts(
    db: Session = Depends(get_session),
    risk_tier: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> ConflictResponse:
    result = _list_verdict_tier_conflicts(
        db,
        risk_tier=risk_tier,
        limit=limit,
    )

    if isinstance(result, Mapping):
        items = list(result.get("items", []))
        total = result.get("total", len(items))
    else:
        items = list(result)
        total = len(items)

    normalized = [
        {
            field: _item_value(item, field)
            for field in ConflictItem.model_fields
        }
        for item in items
    ]
    return ConflictResponse(total=int(total), items=normalized)


if __name__ == "__main__":
    assert any(
        getattr(route, "path", None) == "/api/verdicts/conflicts"
        for route in router.routes
    )
    print("PASS")