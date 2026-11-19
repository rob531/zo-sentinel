from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _datetime_key(value: datetime) -> tuple[bool, float]:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return True, value.timestamp()


def _staleness(age_days: float) -> str:
    if age_days <= 1:
        return "fresh"
    if age_days <= 7:
        return "aging"
    return "stale"


def get_pulse_ageing(db: Session) -> Dict[str, Any]:
    """Return per-pulse threat-intel coverage and staleness information."""
    rows = db.execute(select(ThreatIntelRef)).all()
    grouped: Dict[Any, list[Any]] = {}

    for row in rows:
        pulse_id = row.pulse_id
        grouped.setdefault(pulse_id, []).append(row)

    pulses: list[Dict[str, Any]] = []
    for pulse_id, pulse_rows in grouped.items():
        fetched_values = [
            value
            for value in (_as_datetime(row.fetched_at) for row in pulse_rows)
            if value is not None
        ]
        if not fetched_values:
            continue

        newest = max(fetched_values, key=_datetime_key)
        oldest = min(fetched_values, key=_datetime_key)
        newest_key = _datetime_key(newest)
        oldest_key = _datetime_key(oldest)
        age_days = max(0.0, (newest_key[1] - oldest_key[1]) / 86400.0)

        pulse_created = pulse_rows[0].pulse_created
        indicator_types = sorted(
            {
                row.indicator_type
                for row in pulse_rows
                if row.indicator_type is not None
            }
        )

        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_rows[0].pulse_name,
                "pulse_created": pulse_created,
                "first_fetched_at": oldest,
                "last_fetched_at": newest,
                "age_days": age_days,
                "indicator_count": len(pulse_rows),
                "indicator_types": indicator_types,
                "is_aggregator": any(
                    bool(row.is_aggregator) for row in pulse_rows
                ),
                "staleness": _staleness(age_days),
            }
        )

    pulses.sort(key=lambda item: str(item["pulse_id"]))
    indicator_count = sum(int(pulse["indicator_count"]) for pulse in pulses)
    stale_pulses = sum(1 for pulse in pulses if pulse["staleness"] == "stale")

    return {
        "pulses": pulses,
        "totals": {
            "pulses": len(pulses),
            "indicators": indicator_count,
            "stale_pulses": stale_pulses,
        },
    }


def get_pulse_ageing_report(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers using the report terminology."""
    return get_pulse_ageing(db)


def calculate_pulse_ageing(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers using the calculation terminology."""
    return get_pulse_ageing(db)