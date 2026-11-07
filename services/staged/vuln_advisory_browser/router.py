"""HTTP surface for browsing vulnerability advisories and server matches."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import list_advisories, list_server_advisories

router = APIRouter(prefix="/api", tags=["vuln_advisory_browser"])


class AdvisoryResponse(BaseModel):
    id: int
    feed: str
    summary: str | None = None
    severity: str | None = None
    ecosystem: str | None = None
    package: str | None = None
    affected_ranges: Any = None
    aliases: Any = None
    source_url: str
    published_at: datetime | None = None
    link_count: int
    server_count: int


class AdvisoriesResponse(BaseModel):
    advisories: list[AdvisoryResponse]
    total: int


class ServerAdvisoryResponse(BaseModel):
    id: int
    severity: str | None = None
    package: str | None = None
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: datetime


class ServerAdvisoriesResponse(BaseModel):
    server_id: str
    advisories: list[ServerAdvisoryResponse]


@router.get("/vulns/advisories", response_model=AdvisoriesResponse)
def get_advisories(
    severity: str | None = None,
    ecosystem: str | None = None,
    package: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> AdvisoriesResponse:
    result = list_advisories(
        db,
        severity=severity,
        ecosystem=ecosystem,
        package=package,
        limit=limit,
    )
    return AdvisoriesResponse.model_validate(result)


@router.get(
    "/vulns/servers/{server_id}",
    response_model=ServerAdvisoriesResponse,
)
def get_server_advisories(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerAdvisoriesResponse:
    result = list_server_advisories(db, server_id=server_id)
    return ServerAdvisoriesResponse.model_validate(result)