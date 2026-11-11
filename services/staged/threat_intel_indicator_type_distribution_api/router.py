from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_indicator_type_distribution


router = APIRouter(
    prefix="/api",
    tags=["threat-intel"],
)


class IndicatorTypeDistributionEntry(BaseModel):
    indicator_type: str
    count: int
    distinct_pulses: int
    aggregator_count: int
    newest_fetched_at: str | None
    oldest_fetched_at: str | None


class IndicatorTypeDistributionResponse(BaseModel):
    types: list[IndicatorTypeDistributionEntry]
    total_rows: int
    total_types: int
    generated_at: str


@router.get(
    "/threat-intel/indicator-type-distribution",
    response_model=IndicatorTypeDistributionResponse,
)
def indicator_type_distribution(
    session: Session = Depends(get_session),
) -> IndicatorTypeDistributionResponse:
    return get_indicator_type_distribution(session)


if __name__ == "__main__":
    response = IndicatorTypeDistributionResponse(
        types=[
            IndicatorTypeDistributionEntry(
                indicator_type="domain",
                count=3,
                distinct_pulses=2,
                aggregator_count=1,
                newest_fetched_at="2025-01-01T00:00:00+00:00",
                oldest_fetched_at="2024-12-31T00:00:00+00:00",
            )
        ],
        total_rows=5,
        total_types=3,
        generated_at="2025-01-01T00:00:00+00:00",
    )
    assert response.total_rows == 5
    assert response.total_types == 3
    assert response.types[0].count == 3
    print("PASS")