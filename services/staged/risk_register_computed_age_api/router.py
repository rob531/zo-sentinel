from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_age_distribution

router = APIRouter()


class AgeBucketResponse(BaseModel):
    bucket: str
    count: int


class RiskRegisterComputedAgeResponse(BaseModel):
    buckets: List[AgeBucketResponse]
    total_entries: int
    oldest_computed_at: Optional[datetime]
    newest_computed_at: Optional[datetime]
    stale_over_24h: int


@router.get(
    "/risk-register/age",
    response_model=RiskRegisterComputedAgeResponse,
    summary="Risk-register computed-age distribution",
)
def get_risk_register_computed_age(
    db: Session = Depends(get_session),
) -> RiskRegisterComputedAgeResponse:
    return get_age_distribution(db)


if __name__ == "__main__":
    sample = RiskRegisterComputedAgeResponse(
        buckets=[
            AgeBucketResponse(bucket="<1h", count=1),
            AgeBucketResponse(bucket="1-24h", count=1),
            AgeBucketResponse(bucket="1-7d", count=3),
            AgeBucketResponse(bucket=">7d", count=0),
        ],
        total_entries=5,
        oldest_computed_at=None,
        newest_computed_at=None,
        stale_over_24h=3,
    )
    assert sample.total_entries == 5
    assert sample.stale_over_24h == 3
    assert sample.buckets[-1].count == 0
    assert sample.buckets[0].count == 1
    print("PASS")