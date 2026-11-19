"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import api_key_rotation_age_buckets

router = APIRouter(prefix="/api", tags=["api_key_rotation_age_buckets"])


class Bucket(BaseModel):
    bucket: str
    count: int
    oldest_created_at: datetime
    newest_created_at: datetime


class OrgStat(BaseModel):
    org_id: int
    org_name: str
    key_count: int
    oldest_created_at: datetime


class RotationAgeResponse(BaseModel):
    as_of: datetime
    total_keys: int
    buckets: List[Bucket]
    orgs: List[OrgStat]


@router.get(
    "/keys/rotation-age",
    response_model=RotationAgeResponse,
    summary="API key rotation age buckets",
)
def get_rotation_age(
    org_id: Optional[int] = Query(None, description="Filter by organization ID"),
    days: Optional[int] = Query(
        None, description="Maximum age in days to consider (optional)"
    ),
    as_of: Optional[datetime] = Query(
        None,
        description="ISO timestamp to compute ages against (defaults to now)",
    ),
    db: Session = Depends(get_session),
) -> RotationAgeResponse:
    """
    Return API key age buckets and per‑organization statistics.
    """
    result = api_key_rotation_age_buckets(
        db,
        org_id=org_id,
        days=days,
        as_of=as_of,
    )
    return RotationAgeResponse(**result)