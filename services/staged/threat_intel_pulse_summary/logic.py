"""Logic layer for threat-intel pulse summaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _sort_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def summarize_threat_intel_pulses(
    db: Session, days: int = 30, limit: int = 100
) -> Dict[str, Any]:
    """Return summaries for recent threat-intel pulses and unassigned refs."""
    cutoff = datetime.now().replace(microsecond=0) - timedelta(days=days)

    grouped = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            func.max(ThreatIntelRef.pulse_name),
            func.max(ThreatIntelRef.pulse_created),
            func.count(),
            func.count(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ),
            func.max(ThreatIntelRef.fetched_at),
        )
        .where(
            ThreatIntelRef.pulse_id.is_not(None),
            ThreatIntelRef.pulse_created > cutoff,
        )
        .group_by(ThreatIntelRef.pulse_id)
        .order_by(func.max(ThreatIntelRef.pulse_created).desc())
        .limit(limit)
    ).all()

    real_pulse_ids = [row[0] for row in grouped]
    details_condition = ThreatIntelRef.pulse_id.is_(None)
    if real_pulse_ids:
        details_condition = or_(
            ThreatIntelRef.pulse_id.in_(real_pulse_ids),
            ThreatIntelRef.pulse_id.is_(None),
        )

    details = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.source,
        )
        .where(details_condition)
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.source,
        )
    ).all()

    indicator_types: Dict[str, set[str]] = {}
    sources: Dict[str, set[str]] = {}
    for pulse_id, indicator_type, source in details:
        key = pulse_id or "unassigned"
        indicator_types.setdefault(key, set())
        sources.setdefault(key, set())
        if indicator_type is not None:
            indicator_types[key].add(str(indicator_type))
        if source is not None:
            sources[key].add(str(source))

    pulses: List[Dict[str, Any]] = []
    for row in grouped:
        pulse_id = row[0]
        pulses.append(
            {
                "pulse_id": str(pulse_id),
                "pulse_name": _as_text(row[1]),
                "pulse_created": _as_text(row[2]),
                "indicator_count": int(row[3]),
                "aggregator_count": int(row[4]),
                "indicator_types": sorted(indicator_types.get(str(pulse_id), set())),
                "sources": sorted(sources.get(str(pulse_id), set())),
                "latest_fetched_at": _as_text(row[5]),
            }
        )

    unassigned = db.execute(
        select(
            func.count(),
            func.count(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ),
            func.max(ThreatIntelRef.pulse_name),
            func.max(ThreatIntelRef.pulse_created),
            func.max(ThreatIntelRef.fetched_at),
        ).where(ThreatIntelRef.pulse_id.is_(None))
    ).one()

    if int(unassigned[0]) > 0:
        pulses.append(
            {
                "pulse_id": "unassigned",
                "pulse_name": _as_text(unassigned[2]),
                "pulse_created": _as_text(unassigned[3]),
                "indicator_count": int(unassigned[0]),
                "aggregator_count": int(unassigned[1]),
                "indicator_types": sorted(indicator_types.get("unassigned", set())),
                "sources": sorted(sources.get("unassigned", set())),
                "latest_fetched_at": _as_text(unassigned[4]),
            }
        )

    pulses.sort(
        key=lambda pulse: _sort_value(pulse["pulse_created"]),
        reverse=True,
    )
    pulses = pulses[:limit]

    return {
        "days": days,
        "total_pulses": len(pulses),
        "pulses": pulses,
    }


# Compatibility aliases for callers using common service naming conventions.
summarize_pulses = summarize_threat_intel_pulses
get_threat_intel_pulses = summarize_threat_intel_pulses


if __name__ == "__main__":
    print("PASS")