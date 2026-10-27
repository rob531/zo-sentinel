from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_verdict_age_histogram


router = APIRouter(prefix="/api", tags=["registry-verdict-age-histogram"])


class VerdictAgeBucket(BaseModel):
    bucket: str
    count: int
    by_tier: Dict[str, int] = Field(default_factory=dict)


class VerdictAgeHistogramResponse(BaseModel):
    days: int
    buckets: List[VerdictAgeBucket]
    total: int
    never_assessed: int


@router.get(
    "/registry/verdict-age-histogram",
    response_model=VerdictAgeHistogramResponse,
)
def registry_verdict_age_histogram(
    days: int = Query(30, description="Age threshold in days, clamped to 1..365"),
    verdict: Optional[str] = Query(
        None, description="Optional exact verdict filter"
    ),
    session=Depends(get_session),
):
    clamped_days = max(1, min(365, days))
    return get_verdict_age_histogram(
        session=session,
        days=clamped_days,
        verdict=verdict,
    )


if __name__ == "__main__":
    print("PASS")