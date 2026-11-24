"""router.py -- version drift service HTTP surface."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_version_drift

router = APIRouter(prefix="/api/scores", tags=["scorer-version-drift"])


class ActiveVersion(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str


class VersionDriftEntry(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    row_count: int
    last_scored_at: datetime | None
    avg_p_top: float
    high_risk_share: float


class VersionDriftResponse(BaseModel):
    axis: str
    active: ActiveVersion
    versions: list[VersionDriftEntry]


@router.get("/version_drift", response_model=VersionDriftResponse)
def get_version_drift_response(
    axis: str = Query("overall_risk"),
    db: Session = Depends(get_session),
) -> VersionDriftResponse:
    result = get_version_drift(db, axis)
    return VersionDriftResponse(**result)