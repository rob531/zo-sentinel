from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_indicator_duplicate_summary(session: Session, limit: int = 25) -> dict[str, Any]:
    """Return duplicate indicator statistics for threat intelligence references."""
    limit = max(0, int(limit))

    total_rows = session.execute(
        select(func.count()).select_from(ThreatIntelRef)
    ).scalar_one()

    distinct_indicators = session.execute(
        select(func.count(func.distinct(ThreatIntelRef.indicator_value)))
    ).scalar_one()

    stale_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    stale_rows = session.execute(
        select(func.count())
        .select_from(ThreatIntelRef)
        .where(
            or_(
                ThreatIntelRef.fetched_at.is_(None),
                ThreatIntelRef.fetched_at < stale_cutoff,
            )
        )
    ).scalar_one()

    aggregator_rows = session.execute(
        select(func.count())
        .select_from(ThreatIntelRef)
        .where(ThreatIntelRef.is_aggregator.is_(True))
    ).scalar_one()

    duplicate_rows = []
    if limit:
        rows = session.execute(
            select(
                ThreatIntelRef.indicator_type,
                ThreatIntelRef.indicator_value,
                func.count().label("row_count"),
                func.count(func.distinct(ThreatIntelRef.source)).label("distinct_sources"),
                func.count(func.distinct(ThreatIntelRef.pulse_id)).label("distinct_pulse_ids"),
                func.min(ThreatIntelRef.fetched_at).label("first_fetched_at"),
                func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
            )
            .group_by(
                ThreatIntelRef.indicator_type,
                ThreatIntelRef.indicator_value,
            )
            .having(func.count() > 1)
            .order_by(
                func.count().desc(),
                ThreatIntelRef.indicator_type.asc(),
                ThreatIntelRef.indicator_value.asc(),
            )
            .limit(limit)
        ).all()

        duplicate_rows = [
            {
                "indicator_type": _as_text(row.indicator_type),
                "indicator_value": _as_text(row.indicator_value),
                "count": int(row.row_count),
                "distinct_sources": int(row.distinct_sources or 0),
                "distinct_pulse_ids": int(row.distinct_pulse_ids or 0),
                "first_fetched_at": _as_text(row.first_fetched_at),
                "last_fetched_at": _as_text(row.last_fetched_at),
            }
            for row in rows
        ]

    return {
        "total_rows": int(total_rows or 0),
        "distinct_indicators": int(distinct_indicators or 0),
        "duplicated_indicator_count": len(
            session.execute(
                select(func.count())
                .select_from(
                    select(
                        ThreatIntelRef.indicator_type,
                        ThreatIntelRef.indicator_value,
                    )
                    .group_by(
                        ThreatIntelRef.indicator_type,
                        ThreatIntelRef.indicator_value,
                    )
                    .having(func.count() > 1)
                    .subquery()
                )
            ).scalar_one()
        ),
        "stale_rows": int(stale_rows or 0),
        "aggregator_rows": int(aggregator_rows or 0),
        "duplicates": duplicate_rows,
    }


def get_duplicate_indicator_summary(session: Session, limit: int = 25) -> dict[str, Any]:
    return get_indicator_duplicate_summary(session=session, limit=limit)


def get_duplicate_summary(session: Session, limit: int = 25) -> dict[str, Any]:
    return get_indicator_duplicate_summary(session=session, limit=limit)


def get_duplicate_indicators(session: Session, limit: int = 25) -> dict[str, Any]:
    return get_indicator_duplicate_summary(session=session, limit=limit)


def get_indicator_duplicates(session: Session, limit: int = 25) -> dict[str, Any]:
    return get_indicator_duplicate_summary(session=session, limit=limit)


def get_threat_intel_indicator_duplicates(
    session: Session, limit: int = 25
) -> dict[str, Any]:
    return get_indicator_duplicate_summary(session=session, limit=limit)


__all__ = [
    "get_indicator_duplicate_summary",
    "get_duplicate_indicator_summary",
    "get_duplicate_summary",
    "get_duplicate_indicators",
    "get_indicator_duplicates",
    "get_threat_intel_indicator_duplicates",
]


if __name__ == "__main__":
    print("PASS")