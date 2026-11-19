"""logic.py -- scoring latency percentile computation over the real app models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: list[float], percentile: float) -> float:
    """Return a linearly interpolated percentile without external dependencies."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _distinct_count(values: Iterable[Any]) -> int:
    return len({value for value in values if value is not None})


def get_latency_percentiles(db: Session, hours: int = 24) -> list[dict[str, Any]]:
    """Compute scoring freshness and escalation metrics for each axis.

    Only scores from the requested time window are included. Lag is the age of
    each score at the time of the query and is therefore a freshness proxy.
    """
    if hours <= 0:
        return []

    now = _utc_now()
    cutoff = now - timedelta(hours=hours)
    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.scored_at,
        ).where(
            McpLlmAxisScore.scored_at.is_not(None),
            McpLlmAxisScore.scored_at >= cutoff,
        )
    ).all()

    grouped: dict[str, list[Any]] = {}
    for axis_name, escalated, model_version, decision_rule_version, scored_at in rows:
        grouped.setdefault(axis_name or "unknown", []).append(
            (escalated, model_version, decision_rule_version, scored_at)
        )

    axes: list[dict[str, Any]] = []
    for axis_name, axis_rows in sorted(grouped.items()):
        score_count = len(axis_rows)
        escalated_count = sum(1 for row in axis_rows if bool(row[0]))
        lags = [
            max(0.0, (now - _as_utc(row[3])).total_seconds())
            for row in axis_rows
        ]
        latest_scored_at = max(
            (_as_utc(row[3]) for row in axis_rows),
            default=None,
        )
        mean_lag = sum(lags) / len(lags) if lags else 0.0

        axes.append(
            {
                "axis_name": axis_name,
                "score_count": score_count,
                "escalated_count": escalated_count,
                "escalation_rate": (
                    float(escalated_count / score_count) if score_count else 0.0
                ),
                "mean_lag_seconds": float(mean_lag),
                "p50_lag_seconds": _percentile(lags, 50),
                "p90_lag_seconds": _percentile(lags, 90),
                "p99_lag_seconds": _percentile(lags, 99),
                "distinct_model_versions": _distinct_count(
                    row[1] for row in axis_rows
                ),
                "distinct_decision_rule_versions": _distinct_count(
                    row[2] for row in axis_rows
                ),
                "latest_scored_at": latest_scored_at,
            }
        )

    return axes


def get_scoring_latency_percentiles(
    db: Session, hours: int = 24
) -> list[dict[str, Any]]:
    return get_latency_percentiles(db, hours)


def calculate_latency_percentiles(
    db: Session, hours: int = 24
) -> list[dict[str, Any]]:
    return get_latency_percentiles(db, hours)


def get_latency_metrics(db: Session, hours: int = 24) -> list[dict[str, Any]]:
    return get_latency_percentiles(db, hours)


if __name__ == "__main__":
    assert _percentile([1.0, 2.0, 3.0], 50) == 2.0
    assert _percentile([], 99) == 0.0
    print("PASS")