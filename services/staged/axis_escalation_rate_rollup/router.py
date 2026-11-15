"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_escalation_rate_rollup

router = APIRouter(prefix="/api", tags=["axis_escalation_rate_rollup"])


@router.get("/axis/escalation-rate", response_model=axis_escalation_rate_rollup.__annotations__['return'])
def get_axis_escalation_rate(db: Session = Depends(get_session)):
    return axis_escalation_rate_rollup(db)