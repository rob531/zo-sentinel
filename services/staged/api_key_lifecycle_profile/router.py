"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_lifecycle_profile

router = APIRouter(prefix="/api/api-keys", tags=["api_key_lifecycle_profile"])


class OrgProfile(BaseModel):
    org_id: int
    org_name: str
    key_count: int
    age_buckets: dict[str, int]
    unlabeled_count: int


class LifecycleProfileResponse(BaseModel):
    total_keys: int
    orgs: list[OrgProfile]


@router.get("/lifecycle-profile", response_model=LifecycleProfileResponse)
def get_profile(db: Session = Depends(get_session)) -> LifecycleProfileResponse:
    """Return a lifecycle profile of API keys across organisations."""
    profile = get_lifecycle_profile(db)
    return LifecycleProfileResponse(**profile)