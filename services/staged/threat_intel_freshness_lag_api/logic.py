from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


Record = tuple[Any, Any, datetime | None, datetime | None]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lag_hours(fetched_at: datetime, pulse_created: datetime) -> float:
    return (
        _as_utc(fetched_at) - _as_utc(pulse_created)
    ).total_seconds() / 3600.0


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _sort_key(value: Any) -> tuple[bool, str]:
    if value is None:
        return (True, "")
    return (False, str(value))


def _summarize_records(records: Iterable[Record]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any], dict[str, Any]] = {}

    for source, indicator_type, fetched_at, pulse_created in records:
        key = (source, indicator_type)
        group = grouped.setdefault(
            key,
            {
                "source": source,
                "indicator_type": indicator_type,
                "rows": 0,
                "lags": [],
                "never_fetched": 0,
            },
        )
        group["rows"] += 1

        if fetched_at is None:
            group["never_fetched"] += 1
        elif pulse_created is not None:
            group["lags"].append(_lag_hours(fetched_at, pulse_created))

    results: list[dict[str, Any]] = []
    for key in sorted(
        grouped,
        key=lambda item: (_sort_key(item[0]), _sort_key(item[1])),
    ):
        group = grouped[key]
        lags = group["lags"]
        results.append(
            {
                "source": group["source"],
                "indicator_type": group["indicator_type"],
                "rows": group["rows"],
                "median_lag_hours": _median(lags) if lags else None,
                "max_lag_hours": max(lags) if lags else None,
                "never_fetched": group["never_fetched"],
            }
        )

    return results


def get_threat_intel_freshness_lag(
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    records = db.execute(
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.fetched_at,
            ThreatIntelRef.pulse_created,
        )
    ).all()
    return _summarize_records(records)


get_threat_intel_freshness_lag_data = get_threat_intel_freshness_lag
get_freshness_lag = get_threat_intel_freshness_lag
get_freshness_lag_stats = get_threat_intel_freshness_lag
compute_threat_intel_freshness_lag = get_threat_intel_freshness_lag


if __name__ == "__main__":
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sample = _summarize_records(
        [
            ("alpha", "ip", created.replace(hour=1), created),
            ("alpha", "ip", created.replace(hour=3), created),
            ("alpha", "ip", created.replace(hour=5), created),
            ("alpha", "ip", None, created),
        ]
    )
    assert sample == [
        {
            "source": "alpha",
            "indicator_type": "ip",
            "rows": 4,
            "median_lag_hours": 3.0,
            "max_lag_hours": 5.0,
            "never_fetched": 1,
        }
    ]
    print("PASS")