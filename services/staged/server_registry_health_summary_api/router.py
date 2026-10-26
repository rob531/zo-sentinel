from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_health_summary


router = APIRouter(prefix="/api")


class HealthSummaryResponse(BaseModel):
    total_servers: int
    by_verdict: dict[str, int]
    by_risk_tier: dict[str, int]
    never_scored: int
    stale_assessed: int
    avg_trust_score: float | None
    avg_confidence: float | None


@router.get(
    "/registry/health-summary",
    response_model=HealthSummaryResponse,
)
def health_summary(
    stale_days: int = Query(30, ge=0),
    db: Session = Depends(get_session),
) -> HealthSummaryResponse:
    return get_health_summary(db=db, stale_days=stale_days)


if __name__ == "__main__":
    print("PASS")