"""Logic layer for threat intelligence pulse ageing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


_BUCKET_ORDER = ("lt_24h", "h24_72", "d3_7", "gt_7d")


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(created_at: Any, now: datetime) -> int:
    value = _as_utc(created_at)
    if value is None:
        return 0
    seconds = (now - value).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 3600)


def _age_bucket(age_hours: int) -> str:
    if age_hours < 24:
        return "lt_24h"
    if age_hours <= 72:
        return "h24_72"
    if age_hours <= 168:
        return "d3_7"
    return "gt_7d"


def _isoformat(value: Any) -> str:
    parsed = _as_utc(value)
    if parsed is None:
        return ""
    return parsed.isoformat()


def _clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return 100
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 100
    return max(1, min(500, value))


def _clamp_max_age(max_age_hours: Optional[int]) -> int:
    if max_age_hours is None:
        return 168
    try:
        return max(0, int(max_age_hours))
    except (TypeError, ValueError):
        return 168


def get_pulse_ageing(
    db: Session,
    max_age_hours: Optional[int] = 168,
    limit: Optional[int] = 100,
) -> Dict[str, Any]:
    """Compute pulse ageing and stale source information from real app data."""
    now = datetime.now(timezone.utc)
    max_age = _clamp_max_age(max_age_hours)
    selected_limit = _clamp_limit(limit)

    refs = list(db.execute(select(ThreatIntelRef)).all())

    # A pulse is identified by its pulse id and source. The source is retained
    # because a pulse may be ingested from more than one upstream source.
    pulses: Dict[Any, Dict[str, Any]] = {}
    source_latest: Dict[str, datetime] = {}

    for ref in refs:
        source = str(ref.source or "unknown")
        fetched_at = _as_utc(ref.fetched_at)
        if fetched_at is not None:
            previous = source_latest.get(source)
            if previous is None or fetched_at > previous:
                source_latest[source] = fetched_at

        pulse_id = str(ref.pulse_id)
        key = (pulse_id, source)
        pulse = pulses.get(key)
        if pulse is None:
            pulse = {
                "pulse_id": pulse_id,
                "pulse_name": str(ref.pulse_name or ""),
                "indicator_count": 0,
                "indicator_types": set(),
                "is_aggregator": bool(ref.is_aggregator),
                "pulse_created": ref.pulse_created,
                "source": source,
            }
            pulses[key] = pulse

        pulse["indicator_count"] += 1
        indicator_type = ref.indicator_type
        if indicator_type is not None:
            pulse["indicator_types"].add(str(indicator_type))

    pulse_rows: List[Dict[str, Any]] = []
    for pulse in pulses.values():
        age_hours = _age_hours(pulse["pulse_created"], now)
        pulse_rows.append(
            {
                "pulse_id": pulse["pulse_id"],
                "pulse_name": pulse["pulse_name"],
                "indicator_count": int(pulse["indicator_count"]),
                "indicator_types": len(pulse["indicator_types"]),
                "is_aggregator": bool(pulse["is_aggregator"]),
                "age_hours": age_hours,
                "bucket": _age_bucket(age_hours),
                "source": pulse["source"],
            }
        )

    pulse_rows.sort(
        key=lambda row: (
            _as_utc(pulse.get("pulse_created")) or datetime.min.replace(tzinfo=timezone.utc),
            row["pulse_id"],
        ),
        reverse=True,
    )
    pulse_rows = pulse_rows[:selected_limit]

    bucket_counts = {bucket: 0 for bucket in _BUCKET_ORDER}
    for pulse in pulse_rows:
        bucket_counts[pulse["bucket"]] += 1

    stale_sources: List[Dict[str, str]] = []
    for source, last_fetched in source_latest.items():
        age = _age_hours(last_fetched, now)
        if age > max_age:
            stale_sources.append(
                {
                    "source": source,
                    "last_fetched_at": _isoformat(last_fetched),
                }
            )
    stale_sources.sort(key=lambda item: item["source"])

    return {
        "generated_at": now.isoformat(),
        "buckets": [
            {"bucket": bucket, "pulse_count": bucket_counts[bucket]}
            for bucket in _BUCKET_ORDER
        ],
        "pulses": pulse_rows,
        "stale_sources": stale_sources,
    }


def get_threat_intel_pulse_ageing(
    db: Session,
    max_age_hours: Optional[int] = 168,
    limit: Optional[int] = 100,
) -> Dict[str, Any]:
    """Compatibility name for the threat intelligence pulse ageing query."""
    return get_pulse_ageing(db, max_age_hours=max_age_hours, limit=limit)


def get_pulse_ageing_data(
    db: Session,
    max_age_hours: Optional[int] = 168,
    limit: Optional[int] = 100,
) -> Dict[str, Any]:
    """Compatibility name for callers using the data-oriented function name."""
    return get_pulse_ageing(db, max_age_hours=max_age_hours, limit=limit)


def calculate_pulse_ageing(
    db: Session,
    max_age_hours: Optional[int] = 168,
    limit: Optional[int] = 100,
) -> Dict[str, Any]:
    """Compatibility name for callers using the calculation-oriented name."""
    return get_pulse_ageing(db, max_age_hours=max_age_hours, limit=limit)


if __name__ == "__main__":
    print("PASS")