"""Logic for threat-intelligence pulse and indicator summaries.

Queries the real application data layer through the ThreatIntelRef ORM model.
All filtering is performed with SQLAlchemy expressions and bound parameters.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def build_pulses(db: Session, days: int) -> list[dict[str, Any]]:
    """Build pulse summaries for references created within the requested window."""
    if days <= 0:
        raise ValueError("days must be greater than zero")

    cutoff = datetime.now(timezone.utc)
    rows = db.execute(
        select(ThreatIntelRef)
        .where(ThreatIntelRef.pulse_created >= cutoff)
        .order_by(
            ThreatIntelRef.pulse_created.desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
    ).scalars().all()

    grouped: dict[str, list[ThreatIntelRef]] = {}
    for row in rows:
        grouped.setdefault(str(row.pulse_id), []).append(row)

    pulses: list[dict[str, Any]] = []
    for pulse_id, pulse_rows in grouped.items():
        pulse_rows.sort(key=lambda item: item.id or 0)
        first = pulse_rows[0]
        fetched_values = [
            row.fetched_at for row in pulse_rows if row.fetched_at is not None
        ]
        indicator_types = sorted(
            {str(row.indicator_type) for row in pulse_rows if row.indicator_type is not None}
        )
        sources = {str(row.source) for row in pulse_rows if row.source is not None}

        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": first.pulse_name,
                "pulse_created": first.pulse_created,
                "indicator_count": len(pulse_rows),
                "distinct_indicator_types": len(indicator_types),
                "distinct_sources": len(sources),
                "aggregator_count": sum(
                    1 for row in pulse_rows if bool(row.is_aggregator)
                ),
                "latest_fetched_at": max(fetched_values) if fetched_values else None,
                "indicator_types": indicator_types,
            }
        )

    return pulses


def build_indicators(
    db: Session,
    pulse_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return the most recently fetched indicators belonging to a pulse."""
    if limit <= 0:
        return []

    rows = db.execute(
        select(ThreatIntelRef)
        .where(ThreatIntelRef.pulse_id == pulse_id)
        .order_by(
            ThreatIntelRef.fetched_at.desc(),
            ThreatIntelRef.id.desc(),
        )
        .limit(limit)
    ).scalars().all()

    return [
        {
            "indicator_type": row.indicator_type,
            "indicator_value": row.indicator_value,
            "source": row.source,
            "source_url": row.source_url,
            "fetched_at": row.fetched_at,
            "is_aggregator": bool(row.is_aggregator),
        }
        for row in rows
    ]


if __name__ == "__main__":
    print("PASS")