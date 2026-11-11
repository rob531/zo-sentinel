from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

AXIS_LABELS = {
    "overall_risk": "Overall Risk",
    "auth_strength": "Auth Strength",
    "capability_breadth": "Capability Breadth",
    "data_sensitivity": "Data Sensitivity",
    "network_egress": "Network Egress",
    "maintainer_trust": "Maintainer Trust",
    "exploit_surface": "Exploit Surface",
}


def get_escalated_ratio(
    session: Any,
    axis_name: Optional[str] = None,
    days: Optional[int] = 30,
) -> List[Dict[str, Any]]:
    """Return escalated ratios for the requested axis or all populated axes."""
    requested_axis = axis_name.strip() if isinstance(axis_name, str) else None
    if requested_axis == "":
        requested_axis = None

    query = (
        session.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
    )

    if requested_axis is not None:
        query = query.filter(McpLlmAxisScore.axis_name == requested_axis)

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
        query = query.filter(McpLlmAxisScore.scored_at >= cutoff)

    records = query.all()

    grouped: Dict[str, List[Any]] = {}
    for record in records:
        name = record[0]
        if name not in AXIS_NAMES:
            continue
        grouped.setdefault(name, []).append((record[1], record[2]))

    result: List[Dict[str, Any]] = []
    for name in AXIS_NAMES:
        axis_records = grouped.get(name)
        if not axis_records:
            continue

        total = len(axis_records)
        escalated_records = [
            (escalated, escalated_to)
            for escalated, escalated_to in axis_records
            if bool(escalated)
        ]
        escalated = len(escalated_records)

        target_counts: Dict[str, int] = {}
        for _, escalated_to in escalated_records:
            if escalated_to is None:
                continue
            target = str(escalated_to)
            target_counts[target] = target_counts.get(target, 0) + 1

        if target_counts:
            top_escalated_to = sorted(
                target_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[0][0]
        else:
            top_escalated_to = None

        result.append(
            {
                "axis_name": name,
                "label": AXIS_LABELS.get(name, name.replace("_", " ").title()),
                "total": total,
                "escalated": escalated,
                "escalated_ratio": escalated / total if total else 0.0,
                "top_escalated_to": top_escalated_to,
            }
        )

    return result


def get_axes(
    session: Any,
    axis_name: Optional[str] = None,
    days: Optional[int] = 30,
) -> List[Dict[str, Any]]:
    return get_escalated_ratio(session, axis_name=axis_name, days=days)


def get_axis_escalated_ratio(
    session: Any,
    axis_name: Optional[str] = None,
    days: Optional[int] = 30,
) -> List[Dict[str, Any]]:
    return get_escalated_ratio(session, axis_name=axis_name, days=days)


if __name__ == "__main__":
    print("PASS")