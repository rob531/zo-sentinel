from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_aggregator_breakdown


class TopIndicator(BaseModel):
    indicator_value: str
    count: int


class AggregatorGroup(BaseModel):
    count: int
    sources: int
    pulses: int
    earliest_pulse_created: Optional[datetime] = None
    latest_pulse_created: Optional[datetime] = None
    top_indicators: List[TopIndicator] = []


class SourceCount(BaseModel):
    source: str
    count: int


class AggregatorBreakdownResponse(BaseModel):
    total_indicators: int
    aggregators: AggregatorGroup
    non_aggregators: AggregatorGroup
    sources: List[SourceCount]
    generated_at: datetime


router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threatintel/aggregators",
    response_model=AggregatorBreakdownResponse,
    summary="Summarize aggregator and non-aggregator threat indicators",
)
def aggregators(
    session: Session = Depends(get_session),
) -> AggregatorBreakdownResponse:
    return get_aggregator_breakdown(session)


if __name__ == "__main__":
    sample = AggregatorBreakdownResponse(
        total_indicators=4,
        aggregators=AggregatorGroup(
            count=2,
            sources=1,
            pulses=2,
            earliest_pulse_created=datetime(2024, 1, 1),
            latest_pulse_created=datetime(2024, 1, 2),
            top_indicators=[TopIndicator(indicator_value="example", count=2)],
        ),
        non_aggregators=AggregatorGroup(
            count=2,
            sources=2,
            pulses=2,
            earliest_pulse_created=datetime(2024, 1, 1),
            latest_pulse_created=datetime(2024, 1, 2),
            top_indicators=[TopIndicator(indicator_value="example", count=2)],
        ),
        sources=[
            SourceCount(source="source-a", count=2),
            SourceCount(source="source-b", count=1),
            SourceCount(source="source-c", count=1),
        ],
        generated_at=datetime(2024, 1, 2),
    )
    assert sample.total_indicators == 4
    assert sample.aggregators.count == 2
    assert sample.non_aggregators.count == 2
    assert len(sample.sources) == 3
    print("PASS")