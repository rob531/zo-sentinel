"""services/staged/axis_label_coverage_report/logic.py

Data/computation layer for the *axis_label_coverage_report* service.

Provides a single pure function that can be called from the router (or
directly in tests) to obtain a JSON‑serialisable report of label coverage per
risk axis.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore

# The seven axes that are considered valid for this report.
_VALID_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


def axis_label_coverage_report(
    db: Session, *, days: int = 30, limit_axes: int = 20
) -> Dict[str, Any]:
    """Return a coverage report for LLM axis labels.

    The report contains, for each of the seven risk axes, the total number of
    scores, how many of those scores have a non‑null label, how many are
    unlabelled, the percentage coverage, and a breakdown of label frequencies.

    Parameters
    ----------
    db:
        SQLAlchemy session obtained via ``Depends(get_session)``.
    days:
        Look‑back window in days (default 30). Only rows with
        ``scored_at >= now() - days`` are considered.
    limit_axes:
        Maximum number of axes to include in the ``axes`` list (default 20).
        The service always includes all seven axes; the limit is applied after
        sorting.

    Returns
    -------
    dict
        JSON‑serialisable structure matching the contract:

        {
            "days": int,
            "generated_at": str,          # ISO‑8601 UTC timestamp
            "axes": [
                {
                    "axis_name": str,
                    "score_count": int,
                    "labelled_count": int,
                    "unlabelled_count": int,
                    "coverage_pct": float,   # rounded to 1 decimal place
                    "labels": [
                        {"label": str, "count": int},
                        ...
                    ],
                },
                ...
            ],
        }
    """
    # --------------------------------------------------------------------- #
    # 1. Determine the temporal cut‑off.
    # --------------------------------------------------------------------- #
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # --------------------------------------------------------------------- #
    # 2. Aggregate total scores and labelled scores per axis.
    # --------------------------------------------------------------------- #
    total_subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.count().label("score_count"),
            func.count(McpLlmAxisScore.label).label("labelled_count"),
        )
        .where(
            McpLlmAxisScore.scored_at >= cutoff,
            McpLlmAxisScore.axis_name.in_(_VALID_AXES),
        )
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    # --------------------------------------------------------------------- #
    # 3. Aggregate label frequencies per axis (ignore NULL labels).
    # --------------------------------------------------------------------- #
    label_subq = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            func.count().label("cnt"),
        )
        .where(
            McpLlmAxisScore.scored_at >= cutoff,
            McpLlmAxisScore.axis_name.in_(_VALID_AXES),
            McpLlmAxisScore.label.is_not(None),
        )
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        .subquery()
    )

    # --------------------------------------------------------------------- #
    # 4. Pull the aggregates into Python structures.
    # --------------------------------------------------------------------- #
    # Totals per axis.
    totals: Dict[str, Dict[str, Any]] = {}
    for axis_name, score_count, labelled_count in db.execute(
        select(
            total_subq.c.axis_name,
            total_subq.c.score_count,
            total_subq.c.labelled_count,
        )
    ).all():
        totals[axis_name] = {
            "score_count": int(score_count),
            "labelled_count": int(labelled_count),
            "unlabelled_count": int(score_count - labelled_count),
            "labels": {},  # will be filled next
        }

    # Label breakdown per axis.
    for axis_name, label, cnt in db.execute(
        select(label_subq.c.axis_name, label_subq.c.label, label_subq.c.cnt)
    ).all():
        if axis_name not in totals:
            # This can happen if an axis has only NULL labels.
            totals[axis_name] = {
                "score_count": 0,
                "labelled_count": 0,
                "unlabelled_count": 0,
                "labels": {},
            }
        totals[axis_name]["labels"][label] = int(cnt)

    # Ensure every valid axis appears, even if absent from the query results.
    for axis in _VALID_AXES:
        totals.setdefault(
            axis,
            {
                "score_count": 0,
                "labelled_count": 0,
                "unlabelled_count": 0,
                "labels": {},
            },
        )

    # --------------------------------------------------------------------- #
    # 5. Build the final list of axis dictionaries.
    # --------------------------------------------------------------------- #
    axes: List[Dict[str, Any]] = []
    for axis_name, data in totals.items():
        score_count = data["score_count"]
        labelled = data["labelled_count"]
        coverage_pct = round((labelled / score_count * 100) if score_count else 0.0, 1)

        # Convert label dict to sorted list.
        label_items = [
            {"label": lbl, "count": cnt} for lbl, cnt in data["labels"].items()
        ]
        label_items.sort(key=lambda x: (-x["count"], x["label"]))

        axes.append(
            {
                "axis_name": axis_name,
                "score_count": score_count,
                "labelled_count": labelled,
                "unlabelled_count": data["unlabelled_count"],
                "coverage_pct": coverage_pct,
                "labels": label_items,
            }
        )

    # Sort as required: unlabelled_count descending, then axis_name ascending.
    axes.sort(key=lambda x: (-x["unlabelled_count"], x["axis_name"]))

    # Apply the optional limit.
    axes = axes[:limit_axes]

    # --------------------------------------------------------------------- #
    # 6. Assemble the final payload.
    # --------------------------------------------------------------------- #
    result: Dict[str, Any] = {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes,
    }
    return result


__all__ = ["axis_label_coverage_report"]