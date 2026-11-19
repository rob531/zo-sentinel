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

from .logic import get_meta_profile, list_meta_profiles

router = APIRouter(prefix="/api", tags=["servers"])


class FacetValue(BaseModel):
    value: str
    count: int


class FacetSummary(BaseModel):
    values: list[FacetValue]
    top: str


class ServerMetaProfile(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    facets: dict[str, str]


class MetaProfileResponse(BaseModel):
    total: int
    facets: dict[str, FacetSummary]
    servers: list[ServerMetaProfile]


@router.get("/servers/meta-profile", response_model=MetaProfileResponse)
def list_meta_profile_index(
    db: Session = Depends(get_session),
    facet: str | None = Query(default=None),
    value: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    include_servers: bool = Query(default=True),
) -> MetaProfileResponse:
    return list_meta_profiles(
        db,
        facet=facet,
        value=value,
        limit=limit,
        include_servers=include_servers,
    )


@router.get(
    "/servers/meta-profile/{server_id}",
    response_model=MetaProfileResponse,
)
def get_meta_profile_index(
    server_id: str,
    db: Session = Depends(get_session),
) -> MetaProfileResponse:
    return get_meta_profile(db, server_id)