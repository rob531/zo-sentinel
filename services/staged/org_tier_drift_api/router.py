from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_tier_drift

router = APIRouter(prefix="/api", tags=["org-tier-drift"])


class WorsenedServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str
    prev_tier: str
    delta: int


class OrgTierDrift(BaseModel):
    generated_at: datetime
    org_id: str
    tier_counts: dict[str, int]
    worsened_count: int
    mean_p_top: float | None = None
    worsened_servers: list[WorsenedServer]


@router.get("/orgs/tier-drift", response_model=OrgTierDrift)
def get_org_tier_drift_endpoint(
    org_id: str,
    db: Session = Depends(get_session),
) -> OrgTierDrift:
    result: Any = get_org_tier_drift(db, org_id)

    if isinstance(result, BaseModel):
        payload = result.model_dump()
    elif isinstance(result, Mapping):
        payload = dict(result)
    elif hasattr(result, "__dict__"):
        payload = dict(result.__dict__)
    else:
        payload = {}

    payload.setdefault("generated_at", datetime.now(timezone.utc))
    payload.setdefault("org_id", org_id)
    payload.setdefault("tier_counts", {})
    payload.setdefault("worsened_count", 0)
    payload.setdefault("worsened_servers", [])

    return OrgTierDrift(**payload)