from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import summarise_sources as _logic_summarise_sources
except ImportError:
    try:
        from .logic import summarize_sources as _logic_summarise_sources
    except ImportError:
        _logic_summarise_sources = None


router = APIRouter(prefix="/api")


class SourceCoverage(BaseModel):
    source: str
    indicator_type: str
    indicators: int
    pulses: int
    aggregator_rows: int
    last_fetched: str | None
    age_days: int | None


class SourceCoverageResponse(BaseModel):
    sources: list[SourceCoverage]
    total_indicators: int
    source_count: int


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _query_source_coverage(session: Session) -> dict[str, Any]:
    source = func.coalesce(ThreatIntelRef.source, "unknown")
    indicator_type = func.coalesce(ThreatIntelRef.indicator_type, "unknown")
    aggregator_case = case(
        (ThreatIntelRef.is_aggregator.is_(True), 1),
        else_=0,
    )

    statement = (
        session.query(
            source.label("source"),
            indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("rows"),
            func.count(distinct(ThreatIntelRef.indicator_value)).label("indicators"),
            func.count(distinct(ThreatIntelRef.pulse_id)).label("pulses"),
            func.sum(aggregator_case).label("aggregator_rows"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched"),
        )
        .group_by(source, indicator_type)
        .order_by(source, indicator_type)
    )

    now = datetime.now(timezone.utc)
    sources: list[dict[str, Any]] = []

    for row in statement.all():
        last_fetched = _as_utc(row.last_fetched)
        age_days = None
        if last_fetched is not None:
            age_days = max(0, (now - last_fetched).days)

        sources.append(
            {
                "source": str(row.source),
                "indicator_type": str(row.indicator_type),
                "indicators": int(row.indicators or 0),
                "pulses": int(row.pulses or 0),
                "aggregator_rows": int(row.aggregator_rows or 0),
                "last_fetched": (
                    last_fetched.isoformat() if last_fetched is not None else None
                ),
                "age_days": age_days,
            }
        )

    return {
        "sources": sources,
        "total_indicators": sum(item["indicators"] for item in sources),
        "source_count": len({item["source"] for item in sources}),
    }


@router.get(
    "/threat-intel/sources",
    response_model=SourceCoverageResponse,
)
def list_threat_intel_sources(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if _logic_summarise_sources is not None:
        return _logic_summarise_sources(session)
    return _query_source_coverage(session)