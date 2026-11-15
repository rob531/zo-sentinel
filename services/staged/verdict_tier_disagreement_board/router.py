from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import verdict_tier_disagreements

router = APIRouter(prefix="/api", tags=["verdict-tier-disagreement"])


class ByPair(BaseModel):
    verdict: str
    risk_tier: str
    count: int


class Sample(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    trust_score: float | None = None
    confidence: float | None = None
    last_assessed: str | None = None


class VerdictTierDisagreementResponse(BaseModel):
    total_flagged: int
    checked: int
    by_pair: list[ByPair]
    samples: list[Sample]


@router.get(
    "/registry/verdict-tier-disagreement",
    response_model=VerdictTierDisagreementResponse,
)
def get_verdict_tier_disagreement(
    db: Session = Depends(get_session),
    limit: int = 200,
) -> VerdictTierDisagreementResponse:
    result = verdict_tier_disagreements(db, limit=limit)
    return VerdictTierDisagreementResponse(
        total_flagged=result["total_flagged"],
        checked=result["checked"],
        by_pair=[ByPair(**pair) for pair in result["by_pair"]],
        samples=[Sample(**sample) for sample in result["samples"]],
    )