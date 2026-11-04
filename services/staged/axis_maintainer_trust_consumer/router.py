"""HTTP surface for the maintainer-trust scoring consumer."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import fetch_maintainer_trust_rows as _fetch_maintainer_trust_rows
except ImportError:
    try:
        from .logic import list_maintainer_trust_rows as _fetch_maintainer_trust_rows
    except ImportError:
        from .logic import get_maintainer_trust_rows as _fetch_maintainer_trust_rows

router = APIRouter(prefix="/api", tags=["axis", "maintainer_trust"])


class MaintainerTrustRow(BaseModel):
    server_id: str
    name: str | None = None
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated: bool | None = None
    escalated_to: str | None = None
    decision_rule_version: str | None = None
    model_version: str
    scored_at: datetime | None = None
    risk_tier: str | None = None


class MaintainerTrustResponse(BaseModel):
    axis: str
    rows: list[MaintainerTrustRow]
    count: int


def _coerce_rows(value: Any) -> list[MaintainerTrustRow]:
    if isinstance(value, dict):
        value = value.get("rows", [])
    if isinstance(value, tuple) and value:
        value = value[0]
    return [MaintainerTrustRow.model_validate(row) for row in value]


@router.get("/axis/maintainer_trust", response_model=MaintainerTrustResponse)
def get_maintainer_trust(
    db: Session = Depends(get_session),
    server_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> MaintainerTrustResponse:
    rows = _coerce_rows(
        _fetch_maintainer_trust_rows(db, server_id=server_id, limit=limit)
    )
    return MaintainerTrustResponse(
        axis="maintainer_trust",
        rows=rows,
        count=len(rows),
    )