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

# The core computation lives in logic.py; we import the public function.
from .logic import threat_association_age_rollup

router = APIRouter(prefix="/api/threat", tags=["threat_association_age_rollup"])


class Bucket(BaseModel):
    bucket: str
    severity: str
    count: int


class PerSource(BaseModel):
    registry_source: str
    servers_with_critical: int
    total_servers: int


class AgeRollupResponse(BaseModel):
    as_of: str
    total_associations: int
    buckets: list[Bucket]
    per_source: list[PerSource]


@router.get("/association-age", response_model=AgeRollupResponse)
def get_association_age_rollup(db: Session = Depends(get_session)) -> AgeRollupResponse:
    """Return threat‑association age roll‑up statistics."""
    data = threat_association_age_rollup(db)
    return AgeRollupResponse(**data)