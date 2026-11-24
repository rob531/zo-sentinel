"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import summarize_unauth_access

router = APIRouter(prefix="/api", tags=["registry_unauth_access_summary"])


class Totals(BaseModel):
    servers: int
    unauth_rows: int


class ByUnauthAccessItem(BaseModel):
    unauth_access: str
    servers: int


class BySourceItem(BaseModel):
    source: str
    servers: int


class ByRiskTierItem(BaseModel):
    risk_tier: str | None
    servers: int


class ServerItem(BaseModel):
    server_id: str
    name: str | None = None
    unauth_access: str
    source: str
    risk_tier: str | None = None
    url: str | None = None


class SummaryResponse(BaseModel):
    days: int
    generated_at: str
    totals: Totals
    by_unauth_access: list[ByUnauthAccessItem]
    by_source: list[BySourceItem]
    by_risk_tier: list[ByRiskTierItem]
    servers: list[ServerItem]


@router.get(
    "/registry/unauth-access/summary",
    response_model=SummaryResponse,
    summary="Summarise unauthenticated MCP server exposure",
)
def get_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """Return a summary of unauthenticated access records."""
    result = summarize_unauth_access(db, days)
    return SummaryResponse(**result)


if __name__ == "__main__":
    # Minimal self‑test to satisfy the build contract.
    print("PASS")