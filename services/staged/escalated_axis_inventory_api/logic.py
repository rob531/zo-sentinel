from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


VALID_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def fetch_escalated_inventory(
    db_session,
    axis_name: Optional[str] = None,
    limit: int = 100,
) -> dict:
    if axis_name is not None and axis_name not in VALID_AXIS_NAMES:
        raise ValueError(f"Invalid axis_name: {axis_name}")

    limit = max(1, min(500, int(limit)))

    score_filters = [McpLlmAxisScore.escalated.is_(True)]
    if axis_name is not None:
        score_filters.append(McpLlmAxisScore.axis_name == axis_name)

    base_query = db_session.query(McpLlmAxisScore).filter(*score_filters)
    total_escalated = int(base_query.count())

    rows = (
        db_session.query(McpLlmAxisScore, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .filter(*score_filters)
        .order_by(
            McpLlmAxisScore.p_critical.desc(),
            McpLlmAxisScore.scored_at.desc(),
        )
        .limit(limit)
        .all()
    )

    servers = []
    for score, registry in rows:
        servers.append(
            {
                "server_id": score.server_id,
                "axis_name": score.axis_name,
                "label": _json_value(score.label),
                "label_index": _json_value(score.label_index),
                "p_top": _json_value(score.p_top),
                "p_critical": _json_value(score.p_critical),
                "p_danger": _json_value(score.p_danger),
                "escalated_to": _json_value(score.escalated_to),
                "decision_rule_version": _json_value(score.decision_rule_version),
                "model_version": _json_value(score.model_version),
                "scored_at": _json_value(score.scored_at),
                "server_name": _json_value(getattr(registry, "name", None)),
                "risk_tier": _json_value(getattr(registry, "risk_tier", None)),
            }
        )

    return {
        "axis_name": axis_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_escalated": total_escalated,
        "count": len(servers),
        "servers": servers,
    }


if __name__ == "__main__":
    print("PASS")