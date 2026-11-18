from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Optional

from app.db import get_session
from app import models

try:
    from app.models import McpServerRegistry as RegistryModel
except ImportError:
    RegistryModel = None
    for candidate in vars(models).values():
        if (
            isinstance(candidate, type)
            and getattr(candidate, "__tablename__", None) == "mcp_server_registry"
        ):
            RegistryModel = candidate
            break
    if RegistryModel is None:
        raise ImportError(
            "No SQLAlchemy model mapped to the mcp_server_registry table was found"
        )

from sqlalchemy.orm import Session


SOURCE_FIELDS = (
    "server_id",
    "name",
    "registry_source",
    "scan_count",
    "last_scanned",
    "last_seen",
    "first_seen",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_since(value: datetime, now: datetime) -> float:
    return max(0.0, (_as_utc(now) - _as_utc(value)).total_seconds() / 86400.0)


def _median_or_zero(values: Iterable[Optional[float]]) -> float:
    materialized = [float(value) for value in values if value is not None]
    if not materialized:
        return 0.0
    return float(median(materialized))


def _compute_sources(
    records: Iterable[Any],
    window_days: int,
    now: datetime,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=window_days)
    grouped: dict[str, list[Any]] = {}

    for record in records:
        if isinstance(record, dict):
            registry_source = record.get("registry_source")
            scan_count = record.get("scan_count")
            last_scanned = record.get("last_scanned")
        elif hasattr(record, "_mapping"):
            registry_source = record._mapping.get("registry_source")
            scan_count = record._mapping.get("scan_count")
            last_scanned = record._mapping.get("last_scanned")
        else:
            registry_source = getattr(record, "registry_source", None)
            scan_count = getattr(record, "scan_count", None)
            last_scanned = getattr(record, "last_scanned", None)

        source = registry_source if registry_source is not None else "unknown"
        grouped.setdefault(str(source), []).append((scan_count, last_scanned))

    sources: list[dict[str, Any]] = []

    for source in sorted(grouped):
        rows = grouped[source]
        never_scanned = 0
        stale = 0
        scanned_recent = 0
        scan_counts: list[Optional[float]] = []
        days_since_scan: list[float] = []

        for scan_count, last_scanned in rows:
            scan_counts.append(scan_count)

            if last_scanned is None:
                never_scanned += 1
                continue

            scanned_at = _as_utc(last_scanned)
            days_since_scan.append(_days_since(scanned_at, now))

            if scanned_at < cutoff:
                stale += 1
            else:
                scanned_recent += 1

        sources.append(
            {
                "source": source,
                "total": len(rows),
                "never_scanned": never_scanned,
                "stale": stale,
                "scanned_recent": scanned_recent,
                "median_scan_count": _median_or_zero(scan_counts),
                "median_days_since_scan": _median_or_zero(days_since_scan),
            }
        )

    return sources


def get_scan_coverage_freshness(
    session: Session,
    window_days: int = 30,
) -> dict[str, Any]:
    if isinstance(window_days, bool) or not isinstance(window_days, int):
        raise ValueError("window_days must be an integer")
    if window_days < 0:
        raise ValueError("window_days must be non-negative")

    now = _utc_now()
    rows = (
        session.query(
            RegistryModel.server_id,
            RegistryModel.name,
            RegistryModel.registry_source,
            RegistryModel.scan_count,
            RegistryModel.last_scanned,
            RegistryModel.last_seen,
            RegistryModel.first_seen,
        )
        .all()
    )

    sources = _compute_sources(rows, window_days, now)

    return {
        "window_days": window_days,
        "generated_at": now.isoformat(),
        "sources": sources,
        "totals": {
            "total": sum(source["total"] for source in sources),
            "never_scanned": sum(source["never_scanned"] for source in sources),
            "stale": sum(source["stale"] for source in sources),
        },
    }


compute_scan_coverage_freshness = get_scan_coverage_freshness
get_freshness_coverage = get_scan_coverage_freshness
get_scan_coverage_freshness_data = get_scan_coverage_freshness
get_freshness_data = get_scan_coverage_freshness
build_scan_coverage_freshness = get_scan_coverage_freshness


if __name__ == "__main__":
    test_now = datetime(2025, 1, 31, tzinfo=timezone.utc)
    test_records = [
        {
            "registry_source": "github",
            "scan_count": 4,
            "last_scanned": test_now - timedelta(days=2),
        },
        {
            "registry_source": "github",
            "scan_count": 8,
            "last_scanned": None,
        },
        {
            "registry_source": "npm",
            "scan_count": 2,
            "last_scanned": test_now - timedelta(days=60),
        },
    ]
    test_sources = _compute_sources(test_records, 30, test_now)
    test_by_source = {item["source"]: item for item in test_sources}

    assert test_by_source["github"]["total"] == 2
    assert test_by_source["github"]["never_scanned"] == 1
    assert test_by_source["github"]["scanned_recent"] == 1
    assert test_by_source["npm"]["total"] == 1
    assert test_by_source["npm"]["stale"] == 1
    assert test_by_source["npm"]["never_scanned"] == 0
    assert test_by_source["github"]["median_scan_count"] == 6.0
    assert test_by_source["npm"]["median_days_since_scan"] == 60.0
    print("PASS")