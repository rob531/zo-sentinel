from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


_AXIS_ORDER = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def build_axis_label_coverage_report(rows: list[dict]) -> dict[str, Any]:
    """Build a label-coverage report from MCP LLM axis score rows."""
    axis_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        axis_name = row.get("axis_name")
        if axis_name is None:
            continue
        axis_rows[str(axis_name)].append(row)

    ordered_axes = [axis for axis in _AXIS_ORDER if axis in axis_rows]
    ordered_axes.extend(
        axis for axis in axis_rows if axis not in _AXIS_ORDER
    )

    report_axes: list[dict[str, Any]] = []
    for axis_name in ordered_axes:
        entries = axis_rows[axis_name]
        label_counts: dict[str, int] = defaultdict(int)
        label_indices: dict[str, list[int]] = defaultdict(list)
        index_labels: dict[int, set[str]] = defaultdict(set)

        for row in entries:
            label = row.get("label")
            if label is None:
                continue
            label = str(label)
            raw_index = row.get("label_index")
            try:
                label_index = int(raw_index)
            except (TypeError, ValueError):
                continue

            label_counts[label] += 1
            label_indices[label].append(label_index)
            index_labels[label_index].add(label)

        labels: list[dict[str, Any]] = []
        for label in sorted(
            label_counts,
            key=lambda value: (min(label_indices[value]), value),
        ):
            indices = label_indices[label]
            labels.append(
                {
                    "label": label,
                    "label_index": min(indices),
                    "count": label_counts[label],
                    "min_label_index": min(indices),
                    "contiguous": False,
                }
            )

        observed_indices = sorted(index_labels)
        if observed_indices:
            max_index = observed_indices[-1]
            missing_indices = [
                index for index in range(max_index + 1) if index not in index_labels
            ]
            contiguous = not missing_indices
        else:
            missing_indices = []
            contiguous = True

        for label in labels:
            label["contiguous"] = contiguous

        duplicate_index_labels = sorted(
            {
                label
                for index, labels_at_index in index_labels.items()
                if len(labels_at_index) > 1
                for label in labels_at_index
            }
        )

        report_axes.append(
            {
                "axis_name": axis_name,
                "distinct_labels": len(label_counts),
                "labels": labels,
                "missing_indices": missing_indices,
                "duplicate_index_labels": duplicate_index_labels,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": report_axes,
        "total_rows": len(rows),
    }


def main() -> int:
    rows = [
        {
            "axis_name": "overall_risk",
            "label": "low",
            "label_index": 0,
            "p_top": 0.2,
            "scored_at": "2024-01-01T00:00:00Z",
        },
        {
            "axis_name": "overall_risk",
            "label": "high",
            "label_index": 1,
            "p_top": 0.8,
            "scored_at": "2024-01-01T00:00:00Z",
        },
        {
            "axis_name": "auth_strength",
            "label": "weak",
            "label_index": 0,
            "p_top": 0.2,
            "scored_at": "2024-01-01T00:00:00Z",
        },
        {
            "axis_name": "auth_strength",
            "label": "medium",
            "label_index": 1,
            "p_top": 0.5,
            "scored_at": "2024-01-01T00:00:00Z",
        },
        {
            "axis_name": "auth_strength",
            "label": "strong",
            "label_index": 1,
            "p_top": 0.9,
            "scored_at": "2024-01-01T00:00:00Z",
        },
    ]

    report = build_axis_label_coverage_report(rows)
    assert len(report["axes"]) >= 2

    by_axis = {axis["axis_name"]: axis for axis in report["axes"]}
    assert by_axis["auth_strength"]["duplicate_index_labels"]
    assert all(
        label["contiguous"]
        for label in by_axis["overall_risk"]["labels"]
    )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())