from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
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


def get_axis_label_coverage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    query = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            func.count(McpLlmAxisScore.id).label("label_count"),
        )
        .where(McpLlmAxisScore.axis_name.in_(KNOWN_AXES))
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.label,
        )
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in session.execute(query):
        axis_name = row.axis_name
        grouped.setdefault(axis_name, []).append(
            {
                "label": row.label,
                "label_index": row.label_index,
                "count": int(row.label_count),
            }
        )

    axes: List[Dict[str, Any]] = []
    total_rows = 0

    for axis_name in sorted(grouped):
        labels = grouped[axis_name]
        rows = sum(item["count"] for item in labels)
        total_rows += rows

        populated_indexes = {
            item["label_index"]
            for item in labels
            if item["label_index"] is not None
        }
        if populated_indexes:
            expected_indexes = set(range(max(populated_indexes) + 1))
            missing_label_indexes = sorted(expected_indexes - populated_indexes)
        else:
            missing_label_indexes = []

        axes.append(
            {
                "axis_name": axis_name,
                "rows": rows,
                "labels": labels,
                "missing_label_indexes": missing_label_indexes,
            }
        )

    return {
        "axes": axes,
        "totals": {
            "axes": len(axes),
            "rows": total_rows,
        },
    }


def get_label_coverage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_axis_label_coverage(session)


def axis_label_coverage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_axis_label_coverage(session)


if __name__ == "__main__":
    print("PASS")