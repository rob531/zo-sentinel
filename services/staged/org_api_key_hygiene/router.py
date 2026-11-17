"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for mounting and delegates data access to logic.py.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import api_key_hygiene

router = APIRouter(prefix="/api/security", tags=["security"])


class OrgApiKeySummary(BaseModel):
    org_id: int
    org_name: str
    key_count: int
    oldest_created_at: datetime | None
    newest_created_at: datetime | None


class ApiKeyHygieneResponse(BaseModel):
    orgs: list[OrgApiKeySummary]
    total_keys: int
    stale_key_count: int


@router.get("/api-keys", response_model=ApiKeyHygieneResponse)
def get_api_keys(db: Session = Depends(get_session)) -> ApiKeyHygieneResponse:
    return ApiKeyHygieneResponse(**api_key_hygiene(db))