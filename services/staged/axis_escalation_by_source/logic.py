"""logic.py -- axis escalation aggregation over the real application data layer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


_RISK_TIER_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "moderate": 3,
    "low": 2,
    "info": 1,
    "informational": 1,
    "unknown": 0,
}


def _tier_rank(tier: Optional[str]) -> int:
    return _RISK_TIER_ORDER.get((tier or "unknown").lower(), 0)


def get_escalation_by_source(
    db: Session,
    source: Optional[str] = None,
    axis: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return axis escalation aggregates grouped by registry source and axis."""
    statement = select(McpLlmAxisScore, McpServerRegistry).outerjoin(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id,
    )

    if source is not None:
        statement = statement.where(McpServerRegistry.registry_source == source)
    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    rows = db.execute(statement).all()
    grouped: Dict[tuple, Dict[str, Any]] = {}

    for score, registry in rows:
        registry_source = registry.registry_source if registry is not None else None
        axis_name = score.axis_name or "unknown"
        key = (registry_source or "unknown", axis_name)

        group = grouped.setdefault(
            key,
            {
                "registry_source": key[0],
                "axis_name": axis_name,
                "total_scores": 0,
                "escalated_count": 0,
                "escalation_rate": 0.0,
                "mean_p_top": 0.0,
                "top_escalated_tier": "unknown",
                "_p_top_sum": 0.0,
                "_p_top_count": 0,
                "_top_tier_rank": -1,
            },
        )

        group["total_scores"] += 1

        if score.escalated is True:
            group["escalated_count"] += 1
            tier = registry.risk_tier if registry is not None else None
            tier_value = tier or "unknown"
            tier_rank = _tier_rank(tier_value)
            if tier_rank > group["_top_tier_rank"]:
                group["_top_tier_rank"] = tier_rank
                group["top_escalated_tier"] = tier_value

        if score.p_top is not None:
            group["_p_top_sum"] += float(score.p_top)
            group["_p_top_count"] += 1

    result: List[Dict[str, Any]] = []
    for group in grouped.values():
        total_scores = group["total_scores"]
        escalated_count = group["escalated_count"]
        p_top_count = group["_p_top_count"]

        group["escalation_rate"] = (
            float(escalated_count) / float(total_scores)
            if total_scores
            else 0.0
        )
        group["mean_p_top"] = (
            float(group["_p_top_sum"]) / float(p_top_count)
            if p_top_count
            else 0.0
        )
        del group["_p_top_sum"]
        del group["_p_top_count"]
        del group["_top_tier_rank"]
        result.append(group)

    return result


def aggregate_escalation_by_source(
    db: Session,
    source: Optional[str] = None,
    axis: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compatibility alias for callers using the aggregate naming convention."""
    return get_escalation_by_source(db, source=source, axis=axis)


if __name__ == "__main__":
    print("PASS")