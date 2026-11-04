"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_risk_tier_changes

router = APIRouter(prefix="/api/risk", tags=["risk"])


class Alert(BaseModel):
    server_id: str
    date: str
    old_tier: str
    new_tier: str
    change_type: str


class AlertsResponse(BaseModel):
    days: int
    alerts: list[Alert]


@router.get("/alerts", response_model=AlertsResponse)
def get_alerts(days: int, db: Session = Depends(get_session)) -> AlertsResponse:
    alerts = get_risk_tier_changes(db, days)
    return AlertsResponse(days=days, alerts=alerts)