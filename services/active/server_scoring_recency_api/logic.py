# deps: fastapi, pydantic, sqlalchemy
"""server_scoring_recency_api.logic -- data layer.

Queries MAX(scored_at) per server_id from mcp_llm_axis_scores joined to
mcp_server_registry. Flags servers not scored in the last N days.
Returns raw dicts; router.py shapes the Pydantic response.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


def get_recency_data(days: int, db: Session) -> dict[str, Any]:
    """
    Returns recency data for all servers that appear in mcp_llm_axis_scores.

    Returns
    -------
    dict
        total_servers      -- count of servers that have ever been scored
        stale_count        -- count of servers whose latest score is older than *days*
        freshness_pct      -- percentage of fresh (not stale) servers
        stale_servers      -- list of stale server dicts
    """
    now = datetime.now(timezone.utc)
    cutoff = now - datetime.timedelta(days=days)

    # Sub-query: MAX(scored_at) per server_id
    max_score_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Join registry -> latest score
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            max_score_subq.c.last_scored,
        )
        .select_from(
            McpServerRegistry.__table__.join(
                max_score_subq,
                McpServerRegistry.server_id == max_score_subq.c.server_id,
            )
        )
    )

    rows = db.execute(stmt).all()
    total_servers = len(rows)
    stale_servers: list[dict[str, Any]] = []

    for row in rows:
        last_scored = row.last_scored
        # Normalise tz-naive datetimes (SQLite may return tz-naive)
        if last_scored is not None and last_scored.tzinfo is None:
            last_scored = last_scored.replace(tzinfo=timezone.utc)
        days_ago = (now - last_scored).days if last_scored else None

        stale_servers.append({
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "last_scored": last_scored,
            "days_ago": days_ago,
        })

    # Filter to stale servers (last_scored < cutoff OR never scored)
    stale_list = [
        s for s in stale_servers
        if s["last_scored"] is None or s["last_scored"] < cutoff
    ]
    stale_count = len(stale_list)

    freshness_pct = round(
        (total_servers - stale_count) / total_servers * 100, 2
    ) if total_servers > 0 else 100.0

    return {
        "total_servers": total_servers,
        "stale_count": stale_count,
        "freshness_pct": freshness_pct,
        "stale_servers": stale_list,
    }
