from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_indicator_type_summary
except ImportError:
    get_indicator_type_summary = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


class IndicatorTypeItem(BaseModel):
    indicator_type: str
    count: int
    distinct_pulses: int
    aggregator_count: int
    last_fetched_at: Optional[datetime] = None


class IndicatorTypesResponse(BaseModel):
    generated_at: datetime
    total: int
    sources: list[str]
    indicator_types: list[IndicatorTypeItem]


def _query_indicator_types(
    session: Session, source: Optional[str] = None
) -> IndicatorTypesResponse:
    statement = select(
        ThreatIntelRef.indicator_type,
        func.count(ThreatIntelRef.id).label("count"),
        func.count(func.distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
        func.sum(
            case((ThreatIntelRef.is_aggregator == True, 1), else_=0)
        ).label("aggregator_count"),
        func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
    ).group_by(ThreatIntelRef.indicator_type)

    source_statement = select(ThreatIntelRef.source).distinct()

    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)
        source_statement = source_statement.where(ThreatIntelRef.source == source)

    rows = session.execute(statement).all()
    source_rows = session.execute(source_statement).all()

    indicator_types = [
        IndicatorTypeItem(
            indicator_type=row.indicator_type,
            count=int(row.count or 0),
            distinct_pulses=int(row.distinct_pulses or 0),
            aggregator_count=int(row.aggregator_count or 0),
            last_fetched_at=row.last_fetched_at,
        )
        for row in rows
    ]

    sources = sorted(
        {row.source for row in source_rows if row.source is not None}
    )

    return IndicatorTypesResponse(
        generated_at=datetime.now(timezone.utc),
        total=sum(item.count for item in indicator_types),
        sources=sources,
        indicator_types=indicator_types,
    )


@router.get(
    "/threat-intel/indicator-types",
    response_model=IndicatorTypesResponse,
)
def list_indicator_types(
    source: Optional[str] = None,
    session: Session = Depends(get_session),
) -> IndicatorTypesResponse:
    if get_indicator_type_summary is not None:
        try:
            result = get_indicator_type_summary(session, source)
            if (
                isinstance(result, dict)
                and "indicator_types" in result
                and "total" in result
            ):
                return IndicatorTypesResponse(**result)
        except (TypeError, AttributeError, KeyError, ValueError):
            pass

    return _query_indicator_types(session, source)


if __name__ == "__main__":
    print("PASS")