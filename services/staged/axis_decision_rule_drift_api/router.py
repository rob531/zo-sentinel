from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_decision_rule_drift_logic
except ImportError:
    try:
        from .logic import get_axis_decision_rule_drift_logic
    except ImportError:
        from .logic import get_decision_rule_drift

router = APIRouter()


class DecisionRuleDriftPair(BaseModel):
    decision_rule_version: Optional[str]
    model_version: Optional[str]
    rows: int
    servers: int
    first_scored_at: Optional[datetime]
    last_scored_at: Optional[datetime]


class DecisionRuleDriftResponse(BaseModel):
    decision_rule_count: int
    model_version_count: int
    unspecified_rows: int
    pairs: List[DecisionRuleDriftPair]


@router.get(
    "/scoring/decision-rule-drift",
    response_model=DecisionRuleDriftResponse,
)
def get_decision_rule_drift(
    session: Session = Depends(get_session),
) -> DecisionRuleDriftResponse:
    return get_decision_rule_drift_logic(session)