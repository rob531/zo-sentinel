"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_server_vuln_summary

router = APIRouter(prefix="/api", tags=["server_vuln_summary"])


class AdvisoryResponse(BaseModel):
    advisory_id: int = Field(..., alias="advisory_id")
    feed: str
    severity: str
    package: str | None = None
    summary: str | None = None
    match_basis: str
    match_confidence: float
    source_url: str
    published_at: datetime


class ServerVulnSummaryResponse(BaseModel):
    server_id: int
    total: int
    by_severity: Dict[str, int]
    by_ecosystem: Dict[str, int]
    max_match_confidence: float
    advisories: List[AdvisoryResponse]


@router.get(
    "/servers/{server_id}/vuln-summary",
    response_model=ServerVulnSummaryResponse,
    responses={404: {"description": "Server not found", "content": {"application/json": {"example": {"detail": "server_not_found"}}}}},
)
def get_vuln_summary(server_id: int, db: Session = Depends(get_session)) -> ServerVulnSummaryResponse:
    """
    Return a vulnerability exposure summary for a given server.

    If the server does not exist (no registry row and no linked advisories),
    a 404 response with `detail: "server_not_found"` is returned.
    """
    result = get_server_vuln_summary(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="server_not_found")
    return ServerVulnSummaryResponse(**result)