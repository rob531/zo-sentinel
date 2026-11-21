from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import decision_rule_version_spread


router = APIRouter(
    prefix="/api/scores",
    tags=["scores"],
)


class RuleVersionSpread(BaseModel):
    version: str | None
    row_count: int
    server_count: int
    first_seen: datetime | None
    last_seen: datetime | None


class ModelVersionSpread(BaseModel):
    version: str | None
    row_count: int
    server_count: int


class DecisionRuleVersionSpreadResponse(BaseModel):
    days: int
    rule_versions: list[RuleVersionSpread]
    model_versions: list[ModelVersionSpread]
    total_rule_versions: int


@router.get(
    "/decision-rule-spread",
    response_model=DecisionRuleVersionSpreadResponse,
)
def get_decision_rule_version_spread(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> DecisionRuleVersionSpreadResponse:
    payload = decision_rule_version_spread(db, days)
    return DecisionRuleVersionSpreadResponse.model_validate(payload)