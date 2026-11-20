"""HTTP surface for registry tier conflict reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_tier_conflicts

router = APIRouter(prefix="/api", tags=["registry"])


class TierConflict(BaseModel):
    server_id: str
    name: str | None = None
    registry_tier: str | None = None
    derived_tier: str
    p_top: float
    scored_at: datetime | None = None
    delta: float


class TierConflictResponse(BaseModel):
    total_conflicts: int
    conflicts: list[TierConflict]


@router.get("/registry/tier-conflicts", response_model=TierConflictResponse)
def get_registry_tier_conflicts(
    limit: int = Query(default=100, ge=1),
    db: Session = Depends(get_session),
) -> TierConflictResponse:
    result: Any = get_tier_conflicts(db, limit=limit)

    if isinstance(result, dict):
        rows = result.get("conflicts", [])
    else:
        rows = result

    conflicts = [TierConflict(**row) for row in rows]
    return TierConflictResponse(total_conflicts=len(conflicts), conflicts=conflicts)


if __name__ == "__main__":
    assert any(
        getattr(route, "path", None) == "/api/registry/tier-conflicts"
        for route in router.routes
    )
    print("PASS")