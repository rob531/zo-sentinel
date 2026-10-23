from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class AxisDistinctLabel(BaseModel):
    label: str
    label_index: Optional[int] = None
    servers: List[str] = Field(default_factory=list)


class AxisLabelDriftEntry(BaseModel):
    axis_name: str
    distinct_labels: List[AxisDistinctLabel] = Field(default_factory=list)
    latest_labels: Dict[str, int] = Field(default_factory=dict)
    model_versions: List[str] = Field(default_factory=list)
    rule_versions: List[str] = Field(default_factory=list)
    servers_with_conflicting_labels: int = 0


class AxisLabelDriftResponse(BaseModel):
    axes: List[AxisLabelDriftEntry] = Field(default_factory=list)


def _normalize_server_id(value: object) -> str:
    return str(value)


def _normalize_optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def get_axis_label_drift(
    session: Session = Depends(get_session),
    axis: Optional[str] = None,
) -> Dict[str, object]:
    """Return the latest observed axis labels and identify conflicting servers.

    A server/axis pair is represented by every score sharing that pair's most
    recent ``scored_at``. This preserves simultaneous labels as conflicts while
    excluding older observations from the current label distribution.

    Conflict detection considers all observations for an axis. This identifies
    servers that have carried more than one distinct label even when an older
    conflicting observation was subsequently replaced by a newer score.
    """

    conflict_statement = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
    ).where(
        McpLlmAxisScore.server_id.is_not(None),
        McpLlmAxisScore.axis_name.is_not(None),
        McpLlmAxisScore.label.is_not(None),
    )

    if axis is not None:
        conflict_statement = conflict_statement.where(
            McpLlmAxisScore.axis_name == axis
        )

    conflict_statement = conflict_statement.distinct()
    conflict_rows = session.execute(conflict_statement).all()

    labels_by_server_axis: Dict[Tuple[str, str], Set[str]] = {}
    for row in conflict_rows:
        server_id = _normalize_server_id(row.server_id)
        axis_name = str(row.axis_name)
        label = _normalize_optional_text(row.label)
        if label is None:
            continue
        labels_by_server_axis.setdefault((server_id, axis_name), set()).add(label)

    conflicting_servers_by_axis: Dict[str, Set[str]] = {}
    for (server_id, axis_name), labels in labels_by_server_axis.items():
        if len(labels) > 1:
            conflicting_servers_by_axis.setdefault(axis_name, set()).add(server_id)

    latest_times = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.axis_name.label("axis_name"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .where(
            McpLlmAxisScore.server_id.is_not(None),
            McpLlmAxisScore.axis_name.is_not(None),
            McpLlmAxisScore.scored_at.is_not(None),
        )
        .group_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
        )
    )

    if axis is not None:
        latest_times = latest_times.where(McpLlmAxisScore.axis_name == axis)

    latest_times = latest_times.subquery()

    latest_statement = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
        .select_from(McpLlmAxisScore)
        .join(
            latest_times,
            and_(
                McpLlmAxisScore.server_id == latest_times.c.server_id,
                McpLlmAxisScore.axis_name == latest_times.c.axis_name,
                McpLlmAxisScore.scored_at == latest_times.c.latest_scored_at,
            ),
        )
    )

    if axis is not None:
        latest_statement = latest_statement.where(
            McpLlmAxisScore.axis_name == axis
        )

    latest_statement = latest_statement.order_by(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label_index,
        McpLlmAxisScore.label,
        McpLlmAxisScore.server_id,
    )

    latest_rows = session.execute(latest_statement).all()

    labels_by_axis: Dict[str, Dict[str, Dict[str, object]]] = {}
    servers_by_axis_label: Dict[Tuple[str, str], Set[str]] = {}
    model_versions_by_axis: Dict[str, Set[str]] = {}
    rule_versions_by_axis: Dict[str, Set[str]] = {}

    for row in latest_rows:
        axis_name = str(row.axis_name)
        server_id = _normalize_server_id(row.server_id)
        label = _normalize_optional_text(row.label)

        axis_labels = labels_by_axis.setdefault(axis_name, {})
        if label is not None:
            label_record = axis_labels.setdefault(
                label,
                {
                    "label_index": row.label_index,
                    "servers": set(),
                },
            )

            current_index = label_record["label_index"]
            incoming_index = row.label_index
            if current_index is None or (
                incoming_index is not None and incoming_index < current_index
            ):
                label_record["label_index"] = incoming_index

            label_record["servers"].add(server_id)
            servers_by_axis_label.setdefault((axis_name, label), set()).add(
                server_id
            )

        model_version = _normalize_optional_text(row.model_version)
        if model_version is not None:
            model_versions_by_axis.setdefault(axis_name, set()).add(model_version)

        rule_version = _normalize_optional_text(row.decision_rule_version)
        if rule_version is not None:
            rule_versions_by_axis.setdefault(axis_name, set()).add(rule_version)

    axes: List[Dict[str, object]] = []

    for axis_name in sorted(labels_by_axis):
        axis_labels = labels_by_axis[axis_name]

        def label_sort_key(item: Tuple[str, Dict[str, object]]) -> Tuple[int, int, str]:
            label, record = item
            index = record["label_index"]
            if index is None:
                return (1, 0, label)
            return (0, int(index), label)

        distinct_labels = []
        for label, record in sorted(axis_labels.items(), key=label_sort_key):
            distinct_labels.append(
                {
                    "label": label,
                    "label_index": record["label_index"],
                    "servers": sorted(record["servers"]),
                }
            )

        latest_labels = {
            label: len(servers_by_axis_label[(axis_name, label)])
            for label in sorted(
                axis_labels,
                key=lambda value: label_sort_key((value, axis_labels[value])),
            )
        }

        axes.append(
            {
                "axis_name": axis_name,
                "distinct_labels": distinct_labels,
                "latest_labels": latest_labels,
                "model_versions": sorted(
                    model_versions_by_axis.get(axis_name, set())
                ),
                "rule_versions": sorted(
                    rule_versions_by_axis.get(axis_name, set())
                ),
                "servers_with_conflicting_labels": len(
                    conflicting_servers_by_axis.get(axis_name, set())
                ),
            }
        )

    return {"axes": axes}


@router.get(
    "/axes/label-drift",
    response_model=AxisLabelDriftResponse,
)
def axis_label_drift_endpoint(
    session: Session = Depends(get_session),
    axis: Optional[str] = Query(None),
) -> Dict[str, object]:
    return get_axis_label_drift(session=session, axis=axis)


if __name__ == "__main__":
    print("PASS")