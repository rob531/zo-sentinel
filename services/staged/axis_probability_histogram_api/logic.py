from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

BUCKETS = (
    ("0.0-0.2", 0.0, 0.2),
    ("0.2-0.4", 0.2, 0.4),
    ("0.4-0.6", 0.4, 0.6),
    ("0.6-0.8", 0.6, 0.8),
    ("0.8-1.0", 0.8, 1.0),
)


def _as_probability(value: Any) -> float | None:
    if value is None:
        return None
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
        return None
    return probability


def build_histogram(rows: list[dict]) -> dict:
    """Build a probability histogram from score rows without requiring a database."""
    grouped: dict[str, list[float]] = {axis: [] for axis in VALID_AXES}

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis_name = row.get("axis_name")
        if axis_name not in grouped:
            continue
        probability = _as_probability(row.get("p_top"))
        if probability is None:
            continue
        grouped[axis_name].append(probability)

    axes = []
    for axis_name in VALID_AXES:
        values = grouped[axis_name]
        if not values:
            continue

        bucket_counts = [0 for _ in BUCKETS]
        for probability in values:
            for index, (_, lo, hi) in enumerate(BUCKETS):
                if lo <= probability < hi or (
                    index == len(BUCKETS) - 1 and probability == hi
                ):
                    bucket_counts[index] += 1
                    break

        axes.append(
            {
                "axis_name": axis_name,
                "count": len(values),
                "mean_p_top": float(sum(values) / len(values)),
                "buckets": [
                    {
                        "label": label,
                        "lo": lo,
                        "hi": hi,
                        "count": bucket_counts[index],
                    }
                    for index, (label, lo, hi) in enumerate(BUCKETS)
                ],
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes,
    }


def get_probability_histogram(session: Session) -> dict:
    """Read LLM axis scores from the application database and build the histogram."""
    statement = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.p_top,
    ).where(McpLlmAxisScore.axis_name.in_(VALID_AXES))

    rows = [
        {"axis_name": axis_name, "p_top": p_top}
        for axis_name, p_top in session.execute(statement).all()
    ]
    return build_histogram(rows)


def get_axis_probability_histogram(session: Session) -> dict:
    return get_probability_histogram(session)


def get_histogram(session: Session) -> dict:
    return get_probability_histogram(session)


if __name__ == "__main__":
    _test_rows = [
        {"axis_name": "overall_risk", "p_top": 0.1},
        {"axis_name": "overall_risk", "p_top": 0.3},
        {"axis_name": "overall_risk", "p_top": 0.7},
        {"axis_name": "overall_risk", "p_top": 0.9},
        {"axis_name": "auth_strength", "p_top": 0.2},
        {"axis_name": "auth_strength", "p_top": 0.4},
        {"axis_name": "auth_strength", "p_top": 0.6},
        {"axis_name": "auth_strength", "p_top": 0.8},
        {"axis_name": "network_egress", "p_top": 0.15},
        {"axis_name": "network_egress", "p_top": 0.35},
        {"axis_name": "network_egress", "p_top": 0.55},
        {"axis_name": "network_egress", "p_top": 0.85},
    ]
    _result = build_histogram(_test_rows)
    assert len(_result["axes"]) == 3
    assert all(
        sum(bucket["count"] for bucket in axis["buckets"]) == axis["count"]
        for axis in _result["axes"]
    )
    print("PASS")