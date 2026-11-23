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
from typing import List

from app.db import get_session

# Logic layer provides the heavy lifting; we simply forward the call.
from .logic import (
    unauth_access_matrix as fetch_matrix,
    unauth_server_detail as fetch_server_detail,
)

router = APIRouter(prefix="/api", tags=["unauth_access_matrix"])


class SourceCount(BaseModel):
    source: str
    count: int


class AccessCount(BaseModel):
    unauth_access: str
    count: int


class CellCount(BaseModel):
    source: str
    unauth_access: str
    count: int


class MatrixResponse(BaseModel):
    total_servers: int
    by_source: List[SourceCount]
    by_access: List[AccessCount]
    cells: List[CellCount]
    updated_at_max: str


class Record(BaseModel):
    url: str
    unauth_access: str
    source: str
    updated_at: str


class DetailResponse(BaseModel):
    server_id: int
    name: str
    risk_tier: str
    verdict: str
    records: List[Record]


@router.get("/unauth/matrix", response_model=MatrixResponse)
def get_matrix(db: Session = Depends(get_session)) -> MatrixResponse:
    """Return the unauthenticated‑access matrix across all registered servers."""
    data = fetch_matrix(db)
    return MatrixResponse(**data)


@router.get("/unauth/servers/{server_id}", response_model=DetailResponse)
def get_server_detail(
    server_id: int, db: Session = Depends(get_session)
) -> DetailResponse:
    """Return per‑server unauthenticated‑access details."""
    data = fetch_server_detail(db, server_id)
    return DetailResponse(**data)