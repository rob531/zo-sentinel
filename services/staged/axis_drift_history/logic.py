"""Data and computation layer for the axis-drift history service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


ALLOWED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _session_from_get_session() -> tuple[Session, Any]:
    """Open the application session when the logic is called without one."""
    session_source = get_session()
    try:
        iterator = iter(session_source)
    except TypeError:
        return session_source, None

    session = next(iterator)
    return session, iterator


def _close_session_source(source: Any) -> None:
    if source is None:
        return
    close = getattr(source, "close", None)
    if close is not None:
        close()


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def fetch_axis_history(
    server_id: str,
    limit: int = 50,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Return the history of the seven known LLM-scored risk axes for a server."""
    try:
        bounded_limit = int(limit)
    except (TypeError, ValueError):
        bounded_limit = 0
    bounded_limit = max(0, min(bounded_limit, 500))

    own_session = False
    session_source = None
    if db is None:
        db, session_source = _session_from_get_session()
        own_session = True

    try:
        rows = (
            db.execute(
                select(McpLlmAxisScore)
                .where(McpLlmAxisScore.server_id == server_id)
                .order_by(desc(McpLlmAxisScore.scored_at))
                .limit(bounded_limit)
            )
            .scalars()
            .all()
        )

        valid_rows = [row for row in rows if row.axis_name in ALLOWED_AXES]
        axes: Dict[str, Dict[str, Any]] = {}

        for axis_name in ALLOWED_AXES:
            axis_rows = [row for row in valid_rows if row.axis_name == axis_name]
            if not axis_rows:
                continue

            points = []
            for row in axis_rows:
                points.append(
                    {
                        "scored_at": row.scored_at,
                        "p_top": _number(row.p_top),
                        "p_critical": _number(row.p_critical),
                        "p_danger": _number(row.p_danger),
                        "escalated": bool(row.escalated),
                        "escalated_to": row.escalated_to,
                        "model_version": row.model_version,
                        "decision_rule_version": row.decision_rule_version,
                    }
                )

            p_top_values = [
                point["p_top"]
                for point in points
                if point["p_top"] is not None
            ]
            if len(p_top_values) >= 2:
                delta_p_top = p_top_values[-1] - p_top_values[0]
            else:
                delta_p_top = 0.0

            axes[axis_name] = {
                "label": axis_rows[0].label,
                "points": points,
                "delta_p_top": float(delta_p_top),
                "max_p_top": (
                    max(p_top_values) if p_top_values else 0.0
                ),
                "escalated_count": sum(
                    1 for point in points if point["escalated"]
                ),
            }

        latest_model_version = (
            valid_rows[0].model_version if valid_rows else None
        )

        return {
            "server_id": server_id,
            "axes": axes,
            "axes_present": len(axes),
            "latest_model_version": latest_model_version,
        }
    finally:
        if own_session:
            _close_session_source(session_source)


if __name__ == "__main__":
    print("PASS")