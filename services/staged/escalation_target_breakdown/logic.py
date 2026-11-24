"""Logic for escalation target breakdown queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class InvalidAxisError(ValueError):
    """Raised when an axis filter is not supported."""


def _validate_axis(axis: str | None) -> str | None:
    if axis is not None and axis not in VALID_AXES:
        raise InvalidAxisError(
            "axis must be one of: " + ", ".join(VALID_AXES)
        )
    return axis


def _build_breakdown(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    by_target: dict[str, dict[str, Any]] = {}
    by_axis: dict[str, dict[str, Any]] = {}
    servers: dict[str, dict[str, Any]] = {}

    for score, registry in rows:
        server_id = str(score.server_id)
        target = str(score.escalated_to)
        axis_name = str(score.axis_name)

        target_entry = by_target.setdefault(
            target,
            {"escalated_to": target, "axis_rows": 0, "servers": set()},
        )
        target_entry["axis_rows"] += 1
        target_entry["servers"].add(server_id)

        axis_entry = by_axis.setdefault(
            axis_name,
            {"axis_name": axis_name, "escalated_rows": 0, "servers": set()},
        )
        axis_entry["escalated_rows"] += 1
        axis_entry["servers"].add(server_id)

        server_entry = servers.setdefault(
            server_id,
            {
                "server_id": server_id,
                "name": registry.name if registry is not None else None,
                "risk_tier": registry.risk_tier if registry is not None else None,
                "escalations": [],
            },
        )
        server_entry["escalations"].append(
            {
                "axis_name": axis_name,
                "escalated_to": target,
                "p_critical": float(score.p_critical or 0.0),
                "p_danger": float(score.p_danger or 0.0),
                "p_top": float(score.p_top or 0.0),
            }
        )

    target_output = [
        {
            "escalated_to": entry["escalated_to"],
            "axis_rows": entry["axis_rows"],
            "servers": len(entry["servers"]),
        }
        for entry in by_target.values()
    ]
    target_output.sort(key=lambda item: (-item["axis_rows"], item["escalated_to"]))

    axis_output = [
        {
            "axis_name": entry["axis_name"],
            "escalated_rows": entry["escalated_rows"],
            "servers": len(entry["servers"]),
        }
        for entry in by_axis.values()
    ]
    axis_output.sort(key=lambda item: (-item["escalated_rows"], item["axis_name"]))

    server_output = list(servers.values())
    server_output.sort(
        key=lambda item: (-len(item["escalations"]), item["server_id"])
    )
    server_output = server_output[:100]

    for server in server_output:
        server["escalations"].sort(
            key=lambda item: (item["axis_name"], item["escalated_to"])
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter_axis": None,
        "total_escalated_rows": len(rows),
        "escalated_servers": len(servers),
        "by_escalated_to": target_output,
        "by_axis": axis_output,
        "servers": server_output,
    }


def escalation_breakdown(
    axis: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Return escalated axis rows grouped by target, axis, and server."""
    filter_axis = _validate_axis(axis)

    session_generator = None
    if db is None:
        session_generator = get_session()
        db = next(session_generator)

    try:
        statement = (
            select(McpLlmAxisScore, McpServerRegistry)
            .outerjoin(
                McpServerRegistry,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            )
            .where(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.escalated_to.is_not(None),
                McpLlmAxisScore.escalated_to != "",
            )
        )
        if filter_axis is not None:
            statement = statement.where(
                McpLlmAxisScore.axis_name == filter_axis
            )

        rows = db.execute(statement).all()
        result = _build_breakdown(list(rows))
        result["filter_axis"] = filter_axis
        return result
    finally:
        if session_generator is not None:
            close = getattr(session_generator, "close", None)
            if close is not None:
                close()


if __name__ == "__main__":
    try:
        escalation_breakdown(axis="not_an_axis")
    except InvalidAxisError:
        print("PASS")
    else:
        raise AssertionError("invalid axis was accepted")