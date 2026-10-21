# services/staged/score_lineage_drift/logic.py

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _build_versions(
    session: Session,
    cutoff: datetime,
    axis: Optional[str],
    server_id: Optional[str],
) -> Dict[str, Dict[str, int]]:
    """Return nested dict decision_rule_version -> model_version -> count."""
    q = (
        session.query(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            func.count().label("cnt"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis:
        q = q.filter(McpLlmAxisScore.axis_name == axis)
    if server_id:
        q = q.filter(McpLlmAxisScore.server_id == server_id)

    q = q.group_by(
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
    )
    result: Dict[str, Dict[str, int]] = {}
    for dr, mv, cnt in q:
        result.setdefault(dr, {})[mv] = cnt
    return result


def _build_adapters(
    session: Session,
    cutoff: datetime,
    axis: Optional[str],
    server_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Return list of adapters with count and first/last seen timestamps."""
    q = (
        session.query(
            McpLlmAxisScore.adapter_sha256,
            func.count().label("cnt"),
            func.min(McpLlmAxisScore.scored_at).label("first_seen"),
            func.max(McpLlmAxisScore.scored_at).label("last_seen"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis:
        q = q.filter(McpLlmAxisScore.axis_name == axis)
    if server_id:
        q = q.filter(McpLlmAxisScore.server_id == server_id)

    q = q.group_by(McpLlmAxisScore.adapter_sha256)

    adapters: List[Dict[str, Any]] = []
    for sha, cnt, first, last in q:
        adapters.append(
            {
                "adapter_sha256": sha,
                "count": cnt,
                "first_seen": first,
                "last_seen": last,
            }
        )
    return adapters


def _build_servers(
    session: Session,
    cutoff: datetime,
    axis: Optional[str],
    server_id: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Return the newest score row per (server_id, axis_name) enriched with
    server registry fields.
    """
    # sub‑query that finds the latest scored_at per server/axis
    sub = (
        session.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis:
        sub = sub.filter(McpLlmAxisScore.axis_name == axis)
    if server_id:
        sub = sub.filter(McpLlmAxisScore.server_id == server_id)

    sub = sub.group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name).subquery()

    # join back to fetch the full row and server registry info
    q = (
        session.query(
            McpLlmAxisScore,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
        .join(
            sub,
            and_(
                McpLlmAxisScore.server_id == sub.c.server_id,
                McpLlmAxisScore.axis_name == sub.c.axis_name,
                McpLlmAxisScore.scored_at == sub.c.max_scored,
            ),
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
    )

    servers: List[Dict[str, Any]] = []
    for score, name, registry_source, risk_tier in q:
        servers.append(
            {
                "server_id": score.server_id,
                "name": name,
                "registry_source": registry_source,
                "risk_tier": risk_tier,
                "axis_name": score.axis_name,
                "decision_rule_version": score.decision_rule_version,
                "model_version": score.model_version,
                "adapter_sha256": score.adapter_sha256,
                "scored_at": score.scored_at,
            }
        )
    return servers


def get_lineage(
    session: Session,
    *,
    days: int = 30,
    axis: Optional[str] = None,
    server_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute lineage drift information.

    Parameters
    ----------
    session: SQLAlchemy session (provided via FastAPI Depends(get_session)).
    days: Look‑back window in days (clamped to 1‑365, default 30).
    axis: Optional axis name filter.
    server_id: Optional server identifier filter.

    Returns
    -------
    Dict matching the contract specification.
    """
    # clamp days
    days = max(1, min(365, days))
    cutoff = datetime.utcnow() - timedelta(days=days)

    versions = _build_versions(session, cutoff, axis, server_id)
    adapters = _build_adapters(session, cutoff, axis, server_id)
    servers = _build_servers(session, cutoff, axis, server_id)

    return {
        "days": days,
        "generated_at": datetime.utcnow(),
        "versions": versions,
        "adapters": adapters,
        "servers": servers,
    }


__all__ = ["get_lineage"]