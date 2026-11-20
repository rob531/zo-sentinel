"""HTTP surface for decision-rule version migration reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_decision_rule_version_migration

router = APIRouter(
    prefix="/api",
    tags=["decision-rule-version-migration"],
)


class VersionSummary(BaseModel):
    version: str
    rows: int
    first_seen: str
    last_seen: str


class DecisionRuleVersionsResponse(BaseModel):
    decision_rule_versions: list[VersionSummary]
    model_versions: list[VersionSummary]
    adapter_sha256_count: int
    stale_version_servers: int


@router.get(
    "/scoring/versions",
    response_model=DecisionRuleVersionsResponse,
)
def get_scoring_versions(
    db: Session = Depends(get_session),
) -> DecisionRuleVersionsResponse:
    summary = get_decision_rule_version_migration(db)
    return DecisionRuleVersionsResponse(**summary)