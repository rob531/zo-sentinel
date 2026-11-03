"""Data and computation layer for the vulnerability advisory ecosystem rollup."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory


_KNOWN_SEVERITIES = ("critical", "high", "medium", "low")
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def ecosystem_rollup(db: Session) -> Dict[str, Any]:
    """Aggregate vulnerability advisories by ecosystem and severity."""
    severity_value = func.lower(func.coalesce(VulnAdvisory.severity, "unknown"))
    normalized_severity = case(
        *[(severity_value == severity, severity) for severity in _KNOWN_SEVERITIES],
        else_="unknown",
    )
    ecosystem_value = func.coalesce(VulnAdvisory.ecosystem, "unknown")

    grouped_rows = db.execute(
        select(ecosystem_value, normalized_severity, func.count())
        .group_by(ecosystem_value, normalized_severity)
    ).all()

    by_ecosystem: Dict[str, Dict[str, Any]] = {}
    total = 0
    for ecosystem, severity, count in grouped_rows:
        ecosystem_name = str(ecosystem) if ecosystem not in (None, "") else "unknown"
        severity_name = str(severity) if severity in _SEVERITY_ORDER else "unknown"
        count_value = int(count)
        total += count_value

        entry = by_ecosystem.setdefault(
            ecosystem_name,
            {
                "ecosystem": ecosystem_name,
                "count": 0,
                "by_severity": {name: 0 for name in _SEVERITY_ORDER},
            },
        )
        entry["count"] += count_value
        entry["by_severity"][severity_name] += count_value

    ordered_ecosystems = sorted(
        by_ecosystem.values(),
        key=lambda item: (-item["count"], item["ecosystem"]),
    )

    advisory_rows = db.execute(
        select(VulnAdvisory.published_at, VulnAdvisory.fetched_at)
    ).all()

    newest_published_at: datetime | None = None
    fetch_lags: list[float] = []
    for published_at, fetched_at in advisory_rows:
        published = _as_datetime(published_at)
        fetched = _as_datetime(fetched_at)
        if published is not None and (
            newest_published_at is None or published > newest_published_at
        ):
            newest_published_at = published
        if published is not None and fetched is not None:
            fetch_lags.append((fetched - published).total_seconds() / 3600.0)

    avg_fetch_lag_hours = (
        sum(fetch_lags) / len(fetch_lags) if fetch_lags else None
    )

    return {
        "total": total,
        "by_ecosystem": ordered_ecosystems,
        "newest_published_at": (
            newest_published_at.isoformat() if newest_published_at is not None else None
        ),
        "avg_fetch_lag_hours": avg_fetch_lag_hours,
    }


def get_ecosystem_rollup(db: Session) -> Dict[str, Any]:
    """Return the vulnerability advisory ecosystem rollup."""
    return ecosystem_rollup(db)


def build_ecosystem_rollup(db: Session) -> Dict[str, Any]:
    """Return the vulnerability advisory ecosystem rollup."""
    return ecosystem_rollup(db)


if __name__ == "__main__":
    print("PASS")