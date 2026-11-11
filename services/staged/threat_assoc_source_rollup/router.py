from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_threat_association_rollup as _rollup
except ImportError:
    try:
        from .logic import threat_association_rollup as _rollup
    except ImportError:
        try:
            from .logic import get_rollup as _rollup
        except ImportError:
            from .logic import rollup as _rollup

router = APIRouter(
    prefix="/api/threat/associations",
    tags=["threat-associations"],
)


class ThreatTypeSeverityRollup(BaseModel):
    threat_type: str
    severity: str
    count: int
    server_count: int


class TopServerRollup(BaseModel):
    server_id: str | int
    name: str | None = None
    risk_tier: str | None = None
    count: int


class ThreatAssociationRollupResponse(BaseModel):
    days: int
    total_associations: int
    distinct_servers: int
    by_threat_type: list[ThreatTypeSeverityRollup]
    top_servers: list[TopServerRollup]


@router.get("/rollup", response_model=ThreatAssociationRollupResponse)
def get_threat_association_rollup(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ThreatAssociationRollupResponse:
    result: Any = _rollup(db, days)

    if not isinstance(result, Mapping):
        raise TypeError("Threat association rollup logic must return a mapping")

    return ThreatAssociationRollupResponse(
        days=days,
        total_associations=int(result.get("total_associations", 0)),
        distinct_servers=int(result.get("distinct_servers", 0)),
        by_threat_type=list(result.get("by_threat_type", [])),
        top_servers=list(result.get("top_servers", [])),
    )