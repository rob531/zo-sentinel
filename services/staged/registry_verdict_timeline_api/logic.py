from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _normalized_days(days: int | None) -> int:
    try:
        value = int(days) if days is not None else 30
    except (TypeError, ValueError):
        value = 30
    return max(1, min(365, value))


def compute_timeline(
    rows: Iterable[Any],
    days: int = 30,
    source: str | None = None,
) -> dict[str, Any]:
    """Compute the registry verdict timeline from registry rows.

    ``rows`` may contain SQLAlchemy model instances, mappings, or rows exposing
    the corresponding attributes.  Rows without an assessment date are
    reported as unassessed and are not included in the daily series.
    """
    normalized_days = _normalized_days(days)
    today = date.today()
    first_day = today - timedelta(days=normalized_days - 1)

    grouped: dict[tuple[date, str | None, str | None], int] = {}
    tier_totals: dict[str, int] = {}
    unassessed = 0

    for row in rows:
        assessed = _as_date(_row_value(row, "last_assessed"))
        if assessed is None:
            unassessed += 1
            continue
        if assessed < first_day or assessed > today:
            continue

        verdict_value = _row_value(row, "verdict")
        risk_value = _row_value(row, "risk_tier")
        verdict = None if verdict_value is None else str(verdict_value)
        risk_tier = None if risk_value is None else str(risk_value)

        key = (assessed, verdict, risk_tier)
        grouped[key] = grouped.get(key, 0) + 1
        if risk_tier is not None:
            tier_totals[risk_tier] = tier_totals.get(risk_tier, 0) + 1

    series = [
        {
            "date": assessed.isoformat(),
            "verdict": verdict,
            "risk_tier": risk_tier,
            "count": count,
        }
        for (assessed, verdict, risk_tier), count in sorted(
            grouped.items(),
            key=lambda item: (
                item[0][0],
                "" if item[0][1] is None else item[0][1],
                "" if item[0][2] is None else item[0][2],
            ),
        )
    ]

    return {
        "days": normalized_days,
        "source": source,
        "series": series,
        "tier_totals": dict(sorted(tier_totals.items())),
        "unassessed": unassessed,
    }


def get_verdict_timeline(
    session: Any,
    days: int = 30,
    source: str | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """Read and aggregate the authoritative app registry."""
    normalized_days = _normalized_days(days)
    statement = select(McpServerRegistry)

    if source is not None:
        statement = statement.where(McpServerRegistry.registry_source == source)
    if verdict is not None:
        statement = statement.where(McpServerRegistry.verdict == verdict)

    rows = session.execute(statement).scalars().all()
    return compute_timeline(rows, days=normalized_days, source=source)


get_registry_verdict_timeline = get_verdict_timeline
build_timeline = get_verdict_timeline


if __name__ == "__main__":
    today = date.today()
    yesterday = today - timedelta(days=1)
    fixture = [
        {
            "last_assessed": datetime.combine(yesterday, datetime.min.time()),
            "verdict": "TRUSTED_GENERAL",
            "risk_tier": "trusted",
        },
        {
            "last_assessed": datetime.combine(yesterday, datetime.min.time()),
            "verdict": "CAUTION_LIMITED",
            "risk_tier": "caution",
        },
        {
            "last_assessed": datetime.combine(today, datetime.min.time()),
            "verdict": "HIGH_RISK_ISOLATED",
            "risk_tier": "high",
        },
        {"last_assessed": None, "verdict": None, "risk_tier": None},
    ]

    result = compute_timeline(fixture, days=30)
    assert result["days"] == 30
    assert sum(item["count"] for item in result["series"]) == 3
    assert result["unassessed"] == 1
    assert result["tier_totals"].get("trusted") == 1
    print("PASS")