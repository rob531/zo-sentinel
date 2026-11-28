"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import fetch_unauth_coverage

router = APIRouter(prefix="/api/unauth", tags=["unauth_access_coverage"])


class ServerInfo(BaseModel):
    server_id: int
    name: str
    risk_tier: str
    url: str
    unauth_access: bool
    source: str
    updated_at: str  # ISO‑8601 datetime string


class CoverageResponse(BaseModel):
    total_servers: int
    with_findings: int
    without_findings: int
    coverage_pct: float
    by_tier: Dict[str, int]
    by_source: Dict[str, int]
    servers: List[ServerInfo]
    truncated: bool


@router.get("/coverage", response_model=CoverageResponse)
def get_coverage(db: Session = Depends(get_session)) -> CoverageResponse:
    data = fetch_unauth_coverage(db)
    return CoverageResponse(**data)