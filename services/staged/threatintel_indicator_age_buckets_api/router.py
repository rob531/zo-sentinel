from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_indicator_age_buckets

router = APIRouter(prefix="/api")


class IndicatorAgeBucket(BaseModel):
    bucket: str
    min_days: int
    max_days: Optional[int]
    count: int


class IndicatorTypeCount(BaseModel):
    indicator_type: str
    count: int


class IndicatorAgeBucketsResponse(BaseModel):
    total: int
    buckets: List[IndicatorAgeBucket]
    aggregators: int
    indicator_types: List[IndicatorTypeCount]


@router.get(
    "/threat-intel/indicator-age-buckets",
    response_model=IndicatorAgeBucketsResponse,
)
def indicator_age_buckets(
    session: Session = Depends(get_session),
) -> IndicatorAgeBucketsResponse:
    return get_indicator_age_buckets(session)


if __name__ == "__main__":
    assert any(
        route.path == "/api/threat-intel/indicator-age-buckets"
        for route in router.routes
    )
    print("PASS")