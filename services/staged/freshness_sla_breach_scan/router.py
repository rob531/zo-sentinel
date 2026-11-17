"""HTTP surface for freshness SLA breach reporting."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import breaches as find_breaches

router = APIRouter(prefix="/api/freshness/sla", tags=["freshness"])


class BreachServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_assessed: datetime
    age_hours: float


class BreachResponse(BaseModel):
    max_age_hours: int
    breach_count: int
    worst_age_hours: float | None
    servers: list[BreachServer]


@router.get("/breaches", response_model=BreachResponse)
def breaches(
    max_age_hours: int = 24,
    limit: int = 200,
    db: Session = Depends(get_session),
) -> BreachResponse:
    result = find_breaches(db, max_age_hours=max_age_hours, limit=limit)
    return BreachResponse(**result)