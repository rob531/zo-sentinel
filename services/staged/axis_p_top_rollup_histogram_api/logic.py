from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore
except ImportError:  # pragma: no cover - supports the project's alternate class spelling
    from app.models import McpLlmAxisScore as McpLlmAxisScore


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

_BUCKET_WIDTH = 0.1


def _bucket_index(value: float) -> int:
    """Return the fixed-band index for a p_top value."""
    if value < 0.0:
        return 0
    if value >= 1.0:
        return 9

    for index in range(10):
        lower = index * _BUCKET_WIDTH
        upper = (index + 1) * _BUCKET_WIDTH
        if lower <= value < upper:
            return index
    return 9


def _empty_axes() -> dict[str, dict[str, Any]]:
    axes: dict[str, dict[str, Any]] = {}
    for axis_name in AXIS_NAMES:
        axes[axis_name] = {
            "buckets": [
                {
                    "lo": round(index * _BUCKET_WIDTH, 1),
                    "hi": round((index + 1) * _BUCKET_WIDTH, 1),
                    "count": 0,
                }
                for index in range(10)
            ],
            "scored": 0,
            "mean_p_top": 0.0,
        }
    return axes


def _rollup_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    axes = _empty_axes()

    for row in rows:
        axis_name = row["axis_name"]
        if axis_name not in axes:
            continue

        raw_value = row["p_top"]
        if raw_value is None:
            continue

        value = float(raw_value)
        axes[axis_name]["buckets"][_bucket_index(value)]["count"] += 1
        axes[axis_name]["scored"] += 1

    for axis in axes.values():
        total = axis["scored"]
        if total:
            axis["mean_p_top"] = float(
                sum(
                    bucket["count"] * ((bucket["lo"] + bucket["hi"]) / 2.0)
                    for bucket in axis["buckets"]
                )
                / total
            )
        else:
            axis["mean_p_top"] = 0.0

    return {"axes": axes}


def get_axis_p_top_rollup(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return the fixed-band p_top histogram for the latest score per server/axis."""
    query = text(
        """
        WITH ranked_scores AS (
            SELECT
                server_id,
                axis_name,
                p_top,
                ROW_NUMBER() OVER (
                    PARTITION BY server_id, axis_name
                    ORDER BY scored_at DESC
                ) AS row_number
            FROM mcp_llm_axis_scores
            WHERE axis_name IN (
                'overall_risk',
                'auth_strength',
                'capability_breadth',
                'data_sensitivity',
                'network_egress',
                'maintainer_trust',
                'exploit_surface'
            )
        )
        SELECT axis_name, p_top
        FROM ranked_scores
        WHERE row_number = 1
        """
    )

    result = session.execute(query)
    return _rollup_rows(result.mappings().all())


# Compatibility aliases for callers using the shorter service-specific names.
get_p_top_rollup = get_axis_p_top_rollup
get_axis_rollup = get_axis_p_top_rollup


def _self_test() -> None:
    rows = [
        {"axis_name": "overall_risk", "p_top": 0.05},
        {"axis_name": "overall_risk", "p_top": 0.15},
        {"axis_name": "overall_risk", "p_top": 0.99},
        {"axis_name": "overall_risk", "p_top": 1.0},
    ]

    result = _rollup_rows(rows)
    assert set(result["axes"]) == set(AXIS_NAMES)
    assert len(result["axes"]["overall_risk"]["buckets"]) == 10
    assert result["axes"]["overall_risk"]["scored"] == 4
    assert sum(
        bucket["count"]
        for bucket in result["axes"]["overall_risk"]["buckets"]
    ) == result["axes"]["overall_risk"]["scored"]
    assert result["axes"]["overall_risk"]["buckets"][0]["count"] == 1
    assert result["axes"]["overall_risk"]["buckets"][1]["count"] == 1
    assert result["axes"]["overall_risk"]["buckets"][9]["count"] == 2


if __name__ == "__main__":
    _self_test()
    print("PASS")