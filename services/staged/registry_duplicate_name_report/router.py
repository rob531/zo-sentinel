"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import duplicate_name_report

router = APIRouter(prefix="/api", tags=["registry_duplicate_name_report"])


class ServerInfo(BaseModel):
    server_id: int
    name: str
    registry_source: str
    url: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    last_seen: datetime | None = None
    scan_count: int | None = None


class NameGroup(BaseModel):
    normalized_name: str
    count: int
    servers: List[ServerInfo]


class DuplicateNamesResponse(BaseModel):
    groups: List[NameGroup] = Field(default_factory=list)
    total_groups: int
    total_servers_in_groups: int


@router.get(
    "/registry/duplicate-names",
    response_model=DuplicateNamesResponse,
    summary="Detect duplicate or near‑duplicate server names in the registry",
)
def get_duplicate_names(
    min_group: int = Query(2, ge=1, description="minimum number of servers to form a group"),
    db: Session = Depends(get_session),
) -> DuplicateNamesResponse:
    """
    Return groups of servers whose names normalize to the same string.
    """
    result = duplicate_name_report(db, min_group=min_group)
    return DuplicateNamesResponse(**result)