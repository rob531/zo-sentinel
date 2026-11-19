from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


AsOf = Union[str, date, datetime, None]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_as_of(as_of: AsOf) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    if isinstance(as_of, datetime):
        return _as_utc(as_of)
    if isinstance(as_of, date):
        return datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(as_of, str):
        parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        return _as_utc(parsed)
    raise TypeError("as_of must be an ISO date/datetime string, date, or datetime")


def _staleness_days(newest_fetched_at: datetime, as_of: datetime) -> float:
    seconds = max(0.0, (as_of - newest_fetched_at).total_seconds())
    return round(seconds / 86400.0, 3)


def _header_rows(db: Session) -> List[Any]:
    return list(
        db.execute(
            select(
                ThreatIntelRef.pulse_id,
                ThreatIntelRef.pulse_name,
                ThreatIntelRef.source,
                ThreatIntelRef.pulse_created,
                ThreatIntelRef.is_aggregator,
            )
            .distinct()
            .order_by(
                ThreatIntelRef.pulse_id,
                ThreatIntelRef.pulse_name,
                ThreatIntelRef.source,
                ThreatIntelRef.pulse_created,
                ThreatIntelRef.is_aggregator,
            )
        ).all()
    )


def list_pulse_summaries(db: Session, as_of: AsOf = None) -> List[Dict[str, Any]]:
    """Return one freshness summary for each pulse in threat_intel_refs."""
    effective_as_of = _coerce_as_of(as_of)

    aggregate_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(func.distinct(ThreatIntelRef.indicator_type)).label(
                "indicator_type_count"
            ),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
        )
        .group_by(ThreatIntelRef.pulse_id)
    ).all()

    type_rows = db.execute(
        select(ThreatIntelRef.pulse_id, ThreatIntelRef.indicator_type)
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.pulse_id, ThreatIntelRef.indicator_type)
    ).all()

    types_by_pulse: Dict[Any, List[str]] = {}
    for pulse_id, indicator_type in type_rows:
        types_by_pulse.setdefault(pulse_id, []).append(indicator_type)

    headers_by_pulse: Dict[Any, Any] = {}
    for row in _header_rows(db):
        headers_by_pulse.setdefault(row.pulse_id, row)

    summaries: List[Dict[str, Any]] = []
    for aggregate in aggregate_rows:
        header = headers_by_pulse.get(aggregate.pulse_id)
        if header is None:
            continue

        oldest = _as_utc(aggregate.oldest_fetched_at)
        newest = _as_utc(aggregate.newest_fetched_at)
        indicator_types = types_by_pulse.get(aggregate.pulse_id, [])

        summaries.append(
            {
                "pulse_id": aggregate.pulse_id,
                "pulse_name": header.pulse_name,
                "source": header.source,
                "pulse_created": header.pulse_created,
                "indicator_count": int(aggregate.indicator_count),
                "indicator_types": indicator_types,
                "indicator_type_count": int(aggregate.indicator_type_count),
                "is_aggregator": bool(header.is_aggregator),
                "oldest_fetched_at": oldest,
                "newest_fetched_at": newest,
                "staleness_days": _staleness_days(newest, effective_as_of),
            }
        )

    summaries.sort(key=lambda item: str(item["pulse_id"]))
    return summaries


def get_pulse_indicators(
    db: Session, pulse_id: Any, limit: int = 200
) -> List[Dict[str, Any]]:
    """Return at most 200 indicators belonging to one pulse."""
    bounded_limit = max(0, min(int(limit), 200))
    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.source,
            ThreatIntelRef.fetched_at,
        )
        .where(ThreatIntelRef.pulse_id == pulse_id)
        .order_by(ThreatIntelRef.fetched_at.desc(), ThreatIntelRef.id.asc())
        .limit(bounded_limit)
    ).all()

    return [
        {
            "indicator_type": row.indicator_type,
            "indicator_value": row.indicator_value,
            "source": row.source,
            "fetched_at": _as_utc(row.fetched_at),
        }
        for row in rows
    ]


def get_pulse_detail(
    db: Session, pulse_id: Any, as_of: AsOf = None
) -> Optional[Dict[str, Any]]:
    """Return a pulse header and its indicator catalogue."""
    summary = next(
        (
            pulse
            for pulse in list_pulse_summaries(db, as_of=as_of)
            if pulse["pulse_id"] == pulse_id
        ),
        None,
    )
    if summary is None:
        return None

    return {
        **summary,
        "indicators": get_pulse_indicators(db, pulse_id, limit=200),
    }


def get_pulse(
    db: Session, pulse_id: Any, as_of: AsOf = None
) -> Optional[Dict[str, Any]]:
    return get_pulse_detail(db, pulse_id, as_of=as_of)


def get_pulses(db: Session, as_of: AsOf = None) -> List[Dict[str, Any]]:
    return list_pulse_summaries(db, as_of=as_of)


def get_pulse_catalog(db: Session, as_of: AsOf = None) -> List[Dict[str, Any]]:
    return list_pulse_summaries(db, as_of=as_of)


def get_pulse_summary(
    db: Session, pulse_id: Any, as_of: AsOf = None
) -> Optional[Dict[str, Any]]:
    return next(
        (
            pulse
            for pulse in list_pulse_summaries(db, as_of=as_of)
            if pulse["pulse_id"] == pulse_id
        ),
        None,
    )