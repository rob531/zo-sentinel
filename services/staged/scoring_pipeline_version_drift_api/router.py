"""router.py -- the service's HTTP surface (one concern, one file)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import compute_version_drift

router = APIRouter(prefix="/api", tags=["scoring", "version-drift"])


class VersionDriftEntry(BaseModel):
    model_version: str
    adapter_sha256: Optional[str] = None
    decision_rule_version: Optional[str] = None
    server_count: int
    row_count: int
    first_scored_at: Optional[datetime] = None
    last_scored_at: Optional[datetime] = None


class VersionDriftResponse(BaseModel):
    as_of: Optional[datetime] = None
    versions: list[VersionDriftEntry]
    drifted_server_count: int


@router.get("/scoring/version-drift", response_model=VersionDriftResponse)
def get_version_drift(db: Session = Depends(get_session)) -> VersionDriftResponse:
    payload = compute_version_drift(db)
    return VersionDriftResponse.model_validate(payload)