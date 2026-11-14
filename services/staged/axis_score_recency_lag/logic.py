"""logic.py -- the service's data/computation layer (one concern, one file).

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore


KNOWN_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _parse_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _comparable(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    normalized = _comparable(value)
    return normalized.isoformat().replace("+00:00", "Z")


def compute_axis_recency_lag(
    db_conn: Session,
    now_iso: str,
    hours: int = 24,
    axis: Optional[str] = None,
) -> Dict[str, object]:
    """Return axis score freshness relative to ``now_iso``.

    The function performs read-only ORM queries against
    ``mcp_llm_axis_scores``. ``rows_24h`` is retained as the stable logic
    contract field while ``rows_in_window`` reflects the requested window.
    """
    if hours <= 0:
        raise ValueError("hours must be greater than zero")
    if axis is not None and axis not in KNOWN_AXES:
        raise ValueError(f"unknown axis: {axis}")

    now = _parse_datetime(now_iso)
    if now is None:
        raise ValueError("now_iso must be an ISO-8601 timestamp")
    comparable_now = _comparable(now)
    window_start = comparable_now - timedelta(hours=hours)
    day_start = comparable_now - timedelta(hours=24)

    query = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.scored_at,
    ).where(McpLlmAxisScore.axis_name.in_(KNOWN_AXES))
    if axis is not None:
        query = query.where(McpLlmAxisScore.axis_name == axis)

    rows = db_conn.execute(query).all()
    grouped: Dict[str, List[datetime]] = {}
    for axis_name, scored_at in rows:
        parsed = _parse_datetime(scored_at)
        if parsed is None:
            continue
        grouped.setdefault(str(axis_name), []).append(_comparable(parsed))

    axes: List[Dict[str, object]] = []
    for axis_name in sorted(grouped):
        scored_times = grouped[axis_name]
        newest = max(scored_times)
        lag_seconds = max(0.0, (comparable_now - newest).total_seconds())
        rows_in_window = sum(
            1 for scored_at in scored_times if window_start <= scored_at <= comparable_now
        )
        rows_24h = sum(
            1 for scored_at in scored_times if day_start <= scored_at <= comparable_now
        )
        axes.append(
            {
                "axis_name": axis_name,
                "newest_scored_at": _isoformat(newest),
                "lag_seconds": lag_seconds,
                "rows_24h": rows_24h,
                "rows_in_window": rows_in_window,
            }
        )

    worst_axis: Optional[Dict[str, object]] = None
    if axes:
        worst = max(axes, key=lambda item: (item["lag_seconds"], item["axis_name"]))
        worst_axis = {
            "axis_name": worst["axis_name"],
            "lag_seconds": worst["lag_seconds"],
        }

    return {
        "generated_at": now_iso,
        "window_hours": hours,
        "axes": axes,
        "worst_axis": worst_axis,
    }