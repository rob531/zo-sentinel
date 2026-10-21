from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_aggregator_gap


class AggregatorSource(BaseModel):
    source: str
    is_aggregator: bool
    count: int


class AggregatorGapResponse(BaseModel):
    total_refs: int
    aggregator_refs: int
    direct_refs: int
    distinct_indicator_types: int
    sources: List[AggregatorSource]
    uncovered_indicator_types: List[str]
    latest_pulse_created: Optional[str] = None
    latest_fetched_at: Optional[str] = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threat-intel/aggregator-gap",
    response_model=AggregatorGapResponse,
    summary="Threat intelligence aggregator coverage gap",
)
def aggregator_gap(
    session: Session = Depends(get_session),
    source: Optional[str] = Query(default=None),
    indicator_type: Optional[str] = Query(default=None),
) -> AggregatorGapResponse:
    result = get_aggregator_gap(
        session,
        source=source,
        indicator_type=indicator_type,
    )
    return AggregatorGapResponse(**result)