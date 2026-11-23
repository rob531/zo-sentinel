"""HTTP surface for registry unauthenticated-access reporting."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unauth_access


router = APIRouter(prefix="/api", tags=["registry"])


class SourceCountResponse(BaseModel):
    source: str
    count: int


class UnauthAccessServerResponse(BaseModel):
    server_id: str
    name: str | None
    risk_tier: str | None
    url: str
    unauth_access: str
    updated_at: datetime


class RegistryUnauthAccessResponse(BaseModel):
    sources: list[SourceCountResponse]
    servers: list[UnauthAccessServerResponse]
    total: int


@router.get(
    "/registry/unauth-access",
    response_model=RegistryUnauthAccessResponse,
)
def get_registry_unauth_access(
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> RegistryUnauthAccessResponse:
    result = get_unauth_access(db, source=source, limit=limit)
    return RegistryUnauthAccessResponse(**result)