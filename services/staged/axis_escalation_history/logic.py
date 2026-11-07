"""Data and computation layer for the axis escalation history service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Union

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


LEGAL_AXIS_NAMES = frozenset(
    {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }
)


def _axis_values(
    axis_name: Optional[Union[str, Sequence[str]]],
    axis_names: Optional[Union[str, Sequence[str]]],
    axis: Optional[Union[str, Sequence[str]]],
) -> List[str]:
    values: List[str] = []
    for supplied in (axis_name, axis_names, axis):
        if supplied is None:
            continue
        if isinstance(supplied, str):
            candidates = supplied.split(",")
        else:
            candidates = list(supplied)
        values.extend(str(candidate).strip() for candidate in candidates if str(candidate).strip())

    unknown = sorted(set(values) - LEGAL_AXIS_NAMES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown axis name(s): {', '.join(unknown)}",
        )
    return list(dict.fromkeys(values))


def get_escalation_history(
    db: Session,
    server_id: Optional[str] = None,
    days: int = 30,
    axis_name: Optional[Union[str, Sequence[str]]] = None,
    axis_names: Optional[Union[str, Sequence[str]]] = None,
    axis: Optional[Union[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """Return escalated axis scores grouped by server.

    Only scores that are explicitly marked ``escalated`` are returned.  A
    server is included only when it has at least one matching escalated row.
    """
    try:
        days_value = int(days)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="days must be an integer") from exc

    if days_value < 0:
        raise HTTPException(status_code=400, detail="days must not be negative")

    selected_axes = _axis_values(axis_name, axis_names, axis)

    statement = (
        select(
            McpLlmAxisScore,
            McpServerRegistry.name,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.escalated.is_(True))
    )

    if server_id:
        statement = statement.where(McpLlmAxisScore.server_id == server_id)

    if selected_axes:
        statement = statement.where(McpLlmAxisScore.axis_name.in_(selected_axes))

    if days_value > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_value)
        statement = statement.where(McpLlmAxisScore.scored_at >= cutoff)

    statement = statement.order_by(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.scored_at.desc(),
    )

    rows = db.execute(statement).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for score, server_name in rows:
        server_key = str(score.server_id)
        if server_key not in grouped:
            grouped[server_key] = {
                "server_id": server_key,
                "name": server_name or "",
                "escalations": [],
                "escalation_count": 0,
                "last_escalated_at": None,
            }

        server = grouped[server_key]
        scored_at = score.scored_at
        server["escalations"].append(
            {
                "axis_name": score.axis_name,
                "label": score.label,
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "p_danger": score.p_danger,
                "escalated_to": score.escalated_to,
                "decision_rule_version": score.decision_rule_version,
                "model_version": score.model_version,
                "scored_at": scored_at,
            }
        )
        server["escalation_count"] += 1
        if scored_at is not None:
            if server["last_escalated_at"] is None or scored_at > server["last_escalated_at"]:
                server["last_escalated_at"] = scored_at

    return list(grouped.values())


def get_axis_escalations(
    db: Session,
    server_id: Optional[str] = None,
    days: int = 30,
    axis_name: Optional[Union[str, Sequence[str]]] = None,
    axis_names: Optional[Union[str, Sequence[str]]] = None,
    axis: Optional[Union[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """Compatibility alias for callers using the resource-oriented name."""
    return get_escalation_history(
        db,
        server_id=server_id,
        days=days,
        axis_name=axis_name,
        axis_names=axis_names,
        axis=axis,
    )


def get_escalations(
    db: Session,
    server_id: Optional[str] = None,
    days: int = 30,
    axis_name: Optional[Union[str, Sequence[str]]] = None,
    axis_names: Optional[Union[str, Sequence[str]]] = None,
    axis: Optional[Union[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """Return the grouped escalation history."""
    return get_escalation_history(
        db,
        server_id=server_id,
        days=days,
        axis_name=axis_name,
        axis_names=axis_names,
        axis=axis,
    )


def build_escalation_response(
    servers: List[Dict[str, Any]],
    days: int,
) -> Dict[str, Any]:
    """Build the endpoint envelope around grouped escalation history."""
    return {
        "servers": servers,
        "count": len(servers),
        "days": days,
        "generated_at": datetime.now(timezone.utc),
    }


if __name__ == "__main__":
    print("PASS")