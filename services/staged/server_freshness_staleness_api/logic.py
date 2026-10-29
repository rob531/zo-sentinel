from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from app.db import get_session
from app.models import McpServerRegistry


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    raise TypeError(f"Unsupported datetime value: {value!r}")


def _utc_comparable(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_staleness(rows: list[dict], now: datetime) -> dict:
    """Compute registry-source freshness and staleness statistics.

    A row without ``last_seen`` is counted as never seen and is excluded from
    the median age calculation. The 24-hour, 7-day, and 30-day boundaries are
    inclusive.
    """
    reference = _utc_comparable(now)
    grouped: dict[Any, list[dict]] = {}

    for row in rows:
        source = row.get("registry_source")
        grouped.setdefault(source, []).append(row)

    source_results: list[dict] = []
    for source in sorted(grouped, key=lambda item: (item is None, str(item))):
        source_rows = grouped[source]
        seen_24h = 0
        seen_7d = 0
        stale_30d = 0
        never_seen = 0
        ages_in_hours: list[float] = []

        for row in source_rows:
            last_seen = _as_datetime(row.get("last_seen"))
            if last_seen is None:
                never_seen += 1
                continue

            age_hours = max(
                0.0,
                (reference - _utc_comparable(last_seen)).total_seconds() / 3600.0,
            )
            ages_in_hours.append(age_hours)
            if age_hours <= 24:
                seen_24h += 1
            if age_hours <= 24 * 7:
                seen_7d += 1
            if age_hours > 24 * 30:
                stale_30d += 1

        median_hours = (
            round(float(median(ages_in_hours)), 2) if ages_in_hours else None
        )
        source_results.append(
            {
                "registry_source": source,
                "total": len(source_rows),
                "seen_24h": seen_24h,
                "seen_7d": seen_7d,
                "stale_30d": stale_30d,
                "never_seen": never_seen,
                "median_hours_since_last_seen": median_hours,
            }
        )

    return {
        "sources": source_results,
        "generated_at": now.isoformat(),
    }


def get_staleness(session, now: datetime | None = None) -> dict:
    """Load the authoritative registry rows and compute freshness statistics."""
    records = session.query(McpServerRegistry).all()
    rows = [
        {
            "server_id": record.server_id,
            "registry_source": record.registry_source,
            "first_seen": record.first_seen,
            "last_seen": record.last_seen,
            "last_scanned": record.last_scanned,
            "scan_count": record.scan_count,
        }
        for record in records
    ]
    return compute_staleness(rows, now or datetime.now(timezone.utc))


if __name__ == "__main__":
    from datetime import timedelta

    test_now = datetime(2025, 1, 31, 12, 0, tzinfo=timezone.utc)
    test_rows = [
        {
            "server_id": "one",
            "registry_source": "alpha",
            "last_seen": test_now - timedelta(hours=2),
        },
        {
            "server_id": "two",
            "registry_source": "alpha",
            "last_seen": test_now - timedelta(days=10),
        },
        {
            "server_id": "three",
            "registry_source": "beta",
            "last_seen": None,
        },
        {
            "server_id": "four",
            "registry_source": "beta",
            "last_seen": test_now - timedelta(days=40),
        },
    ]
    result = compute_staleness(test_rows, test_now)
    by_source = {item["registry_source"]: item for item in result["sources"]}
    assert len(result["sources"]) == 2
    assert by_source["beta"]["never_seen"] == 1
    assert by_source["beta"]["stale_30d"] == 1
    assert by_source["alpha"]["seen_24h"] == 1
    print("PASS")