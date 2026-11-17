"""HTTP surface for scoring rule and model version history."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import list_rule_versions

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class RuleVersionItem(BaseModel):
    decision_rule_version: str | None
    model_version: str
    adapter_sha256: str | None
    row_count: int
    server_count: int
    first_scored_at: datetime | None
    last_scored_at: datetime | None


class RuleVersionsResponse(BaseModel):
    versions: list[RuleVersionItem]
    total_versions: int


@router.get("/rule-versions", response_model=RuleVersionsResponse)
def get_rule_versions(db: Session = Depends(get_session)) -> RuleVersionsResponse:
    versions = list_rule_versions(db)
    return RuleVersionsResponse(
        versions=[RuleVersionItem(**version) for version in versions],
        total_versions=len(versions),
    )