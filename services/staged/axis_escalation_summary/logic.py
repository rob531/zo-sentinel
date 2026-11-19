"""logic.py -- data/computation layer for the axis_escalation_summary service.

Provides a single pure function `summarize_escalations` that aggregates
escalation information from the real application tables
`mcp_llm_axis_scores` and `mcp_server_registry`.  The function is
intended to be called from a FastAPI router that supplies a SQLAlchemy
`Session` via the standard `Depends(get_session)` pattern.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


def summarize_escalations(db: Session, window_days: int = 30) -> Dict:
    """
    Summarise rule‑driven score escalations across the 7 risk axes.

    Parameters
    ----------
    db:
        Active SQLAlchemy session (provided by ``app.db.get_session``).
    window_days:
        Number of days in the past to consider (default 30).

    Returns
    -------
    dict
        JSON‑serialisable structure matching the contract:

        {
            "window_days": int,
            "total_escalated": int,
            "by_axis": [
                {
                    "axis_name": str,
                    "label": str,
                    "escalated_count": int,
                    "server_count": int,
                    "targets": [
                        {"escalated_to": str, "count": int},
                        ...
                    ]
                },
                ...
            ],
            "top_servers": [
                {
                    "server_id": str,
                    "name": str,
                    "risk_tier": str,
                    "escalated_axes": [str, ...],
                },
                ...
            ],
            "generated_at": iso8601‑timestamp,
        }
    """
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    # ------------------------------------------------------------------
    # Total escalated rows in the window
    # ------------------------------------------------------------------
    total_escalated = db.execute(
        select(func.count())
        .select_from(McpLlmAxisScore)
        .where(
            and_(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.scored_at >= cutoff,
            )
        )
    ).scalar_one()

    # ------------------------------------------------------------------
    # Aggregation per axis
    # ------------------------------------------------------------------
    axis_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            func.count().label("escalated_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
        )
        .where(
            and_(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.scored_at >= cutoff,
            )
        )
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
    ).all()

    by_axis: List[Dict] = []
    for axis_name, label, escalated_cnt, server_cnt in axis_rows:
        target_rows = db.execute(
            select(
                McpLlmAxisScore.escalated_to,
                func.count().label("cnt"),
            )
            .where(
                and_(
                    McpLlmAxisScore.axis_name == axis_name,
                    McpLlmAxisScore.escalated.is_(True),
                    McpLlmAxisScore.scored_at >= cutoff,
                )
            )
            .group_by(McpLlmAxisScore.escalated_to)
        ).all()

        targets = [
            {"escalated_to": (et if et is not None else "none"), "count": int(cnt)}
            for et, cnt in target_rows
        ]

        by_axis.append(
            {
                "axis_name": axis_name,
                "label": label,
                "escalated_count": int(escalated_cnt),
                "server_count": int(server_cnt),
                "targets": targets,
            }
        )

    # ------------------------------------------------------------------
    # Top servers by number of distinct escalated axes
    # ------------------------------------------------------------------
    # Gather (server_id, axis_name) pairs for escalated rows
    server_axis_pairs = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .where(
            and_(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.scored_at >= cutoff,
            )
        )
    ).all()

    server_to_axes: Dict[str, set] = {}
    for srv_id, axis in server_axis_pairs:
        server_to_axes.setdefault(srv_id, set()).add(axis)

    # Sort servers by count of distinct escalated axes (desc) and take top 5
    top_server_ids = sorted(
        server_to_axes.items(),
        key=lambda item: len(item[1]),
        reverse=True,
    )[:5]

    top_servers: List[Dict] = []
    for srv_id, axes_set in top_server_ids:
        srv = db.get(McpServerRegistry, srv_id)
        if not srv:
            continue
        top_servers.append(
            {
                "server_id": srv_id,
                "name": srv.name,
                "risk_tier": srv.risk_tier,
                "escalated_axes": sorted(list(axes_set)),
            }
        )

    # ------------------------------------------------------------------
    # Assemble final payload
    # ------------------------------------------------------------------
    return {
        "window_days": window_days,
        "total_escalated": int(total_escalated),
        "by_axis": by_axis,
        "top_servers": top_servers,
        "generated_at": datetime.utcnow().isoformat(),
    }