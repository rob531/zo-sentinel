from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore
except ImportError:  # pragma: no cover - supports the acronym spelling used by some revisions
    from app.models import McpLlmAxisScore as McpLlmAxisScore


KNOWN_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

_KNOWN_AXIS_SET = frozenset(KNOWN_AXES)


def _is_escalated(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_escalations(rows: list[dict]) -> dict:
    """Build the escalation statistics response from axis-score rows.

    This helper is intentionally independent of the database so its aggregation
    and response contract can be tested without a live session.
    """
    axis_state: dict[str, dict[str, Any]] = {}
    fleet_total = 0
    fleet_escalated = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        fleet_total += 1
        escaped = _is_escalated(row.get("escalated"))
        if escaped:
            fleet_escalated += 1

        axis_name = row.get("axis_name")
        if axis_name is None:
            continue
        axis_name = str(axis_name)
        if axis_name not in _KNOWN_AXIS_SET:
            continue

        state = axis_state.setdefault(
            axis_name,
            {
                "escalated_count": 0,
                "total_count": 0,
                "targets": {},
                "critical_sum": 0.0,
                "critical_count": 0,
            },
        )
        state["total_count"] += 1

        if not escaped:
            continue

        state["escalated_count"] += 1
        tier = row.get("escalated_to")
        if tier is not None and str(tier) != "":
            tier = str(tier)
            state["targets"][tier] = state["targets"].get(tier, 0) + 1

        p_critical = _as_float(row.get("p_critical"))
        if p_critical is not None:
            state["critical_sum"] += p_critical
            state["critical_count"] += 1

    axes = []
    for axis_name in KNOWN_AXES:
        state = axis_state.get(axis_name)
        if state is None:
            continue

        escalated_count = state["escalated_count"]
        total_count = state["total_count"]
        critical_count = state["critical_count"]
        mean_p_critical = (
            state["critical_sum"] / critical_count if critical_count else 0.0
        )

        axes.append(
            {
                "axis_name": axis_name,
                "escalated_count": escalated_count,
                "total_count": total_count,
                "escalation_rate": (
                    escalated_count / total_count if total_count else 0.0
                ),
                "targets": [
                    {"tier": tier, "count": count}
                    for tier, count in sorted(state["targets"].items())
                ],
                "mean_p_critical": mean_p_critical,
            }
        )

    return {
        "axes": axes,
        "totals": {
            "escalated_rows": fleet_escalated,
            "total_rows": fleet_total,
            "fleet_rate": fleet_escalated / fleet_total if fleet_total else 0.0,
        },
    }


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "axis_name": row.axis_name,
        "escalated": row.escalated,
        "escalated_to": row.escalated_to,
        "server_id": row.server_id,
        "scored_at": row.scored_at,
        "p_critical": row.p_critical,
        "p_danger": row.p_danger,
    }


def get_escalation_path_stats(db: Session = Depends(get_session)) -> dict:
    """Read axis scores from the real app data layer and summarize them."""
    rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
        )
        .all()
    )
    return summarize_escalations([_row_to_dict(row) for row in rows])


def get_escalation_stats(db: Session = Depends(get_session)) -> dict:
    """Compatibility alias for callers using the shorter service name."""
    return get_escalation_path_stats(db)


if __name__ == "__main__":
    sample_rows = [
        {
            "axis_name": "overall_risk",
            "escalated": True,
            "escalated_to": "critical",
            "server_id": "server-1",
            "scored_at": None,
            "p_critical": 0.8,
            "p_danger": 0.4,
        },
        {
            "axis_name": "overall_risk",
            "escalated": False,
            "escalated_to": None,
            "server_id": "server-2",
            "scored_at": None,
            "p_critical": 0.2,
            "p_danger": 0.1,
        },
        {
            "axis_name": "auth_strength",
            "escalated": True,
            "escalated_to": "high",
            "server_id": "server-3",
            "scored_at": None,
            "p_critical": 0.6,
            "p_danger": 0.5,
        },
    ]
    result = summarize_escalations(sample_rows)
    assert result["axes"][0]["axis_name"] == "overall_risk"
    assert result["axes"][0]["escalated_count"] == 1
    assert result["axes"][0]["targets"] == [{"tier": "critical", "count": 1}]
    assert result["axes"][0]["mean_p_critical"] == 0.8
    assert result["totals"]["escalated_rows"] == 2
    assert result["totals"]["total_rows"] == 3
    assert 0.0 <= result["totals"]["fleet_rate"] <= 1.0
    print("PASS")