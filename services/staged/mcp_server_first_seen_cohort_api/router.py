from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpServerRegistry
except ImportError:
    from app.models import McpServerRegistry as McpServerRegistry

try:
    from .logic import get_first_seen_cohort as _get_first_seen_cohort
except (ImportError, ValueError):
    _get_first_seen_cohort = None


router = APIRouter(prefix="/api", tags=["registry"])


class FirstSeenCohortTopSource(BaseModel):
    source: str
    count: int
    date: date


class FirstSeenCohortEntry(BaseModel):
    date: date
    new_servers: int
    by_source: Dict[str, int]
    by_risk_tier: Dict[str, int]


class FirstSeenCohortResponse(BaseModel):
    days: int
    total_new: int
    series: List[FirstSeenCohortEntry]
    top_source: FirstSeenCohortTopSource


def _cohort_date(value: datetime) -> date:
    if value.tzinfo is not None:
        value = value.astimezone().date()
        return value
    return value.date()


def _build_first_seen_cohort(session: Session, days: int) -> Dict:
    now = datetime.utcnow()
    first_day = (now - timedelta(days=days - 1)).date()
    cutoff = datetime.combine(first_day, time.min)

    statement = (
        select(
            McpServerRegistry.first_seen,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
        .where(McpServerRegistry.first_seen >= cutoff)
        .order_by(McpServerRegistry.first_seen.asc())
    )

    grouped: Dict[date, Dict[str, object]] = {}
    for first_seen, registry_source, risk_tier in session.execute(statement):
        if first_seen is None:
            continue

        cohort_date = _cohort_date(first_seen)
        bucket = grouped.setdefault(
            cohort_date,
            {
                "new_servers": 0,
                "sources": Counter(),
                "risk_tiers": Counter(),
            },
        )
        bucket["new_servers"] += 1

        if registry_source is not None:
            bucket["sources"][str(registry_source)] += 1
        if risk_tier is not None:
            bucket["risk_tiers"][str(risk_tier)] += 1

    series: List[Dict] = []
    for cohort_date in sorted(grouped):
        bucket = grouped[cohort_date]
        series.append(
            {
                "date": cohort_date,
                "new_servers": int(bucket["new_servers"]),
                "by_source": dict(sorted(bucket["sources"].items())),
                "by_risk_tier": dict(sorted(bucket["risk_tiers"].items())),
            }
        )

    total_new = sum(entry["new_servers"] for entry in series)

    if series:
        newest = series[-1]
        source_counts = newest["by_source"]
        if source_counts:
            top_count = max(source_counts.values())
            top_name = min(
                source
                for source, count in source_counts.items()
                if count == top_count
            )
            top_source = {
                "source": top_name,
                "count": top_count,
                "date": newest["date"],
            }
        else:
            top_source = {
                "source": "",
                "count": 0,
                "date": newest["date"],
            }
    else:
        top_source = {
            "source": "",
            "count": 0,
            "date": first_day,
        }

    return {
        "days": days,
        "total_new": total_new,
        "series": series,
        "top_source": top_source,
    }


@router.get(
    "/registry/first-seen-cohort",
    response_model=FirstSeenCohortResponse,
    summary="Get first-seen server cohorts by calendar day",
)
def get_first_seen_cohort_endpoint(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Dict:
    return _build_first_seen_cohort(session, days)


if __name__ == "__main__":
    print("PASS")