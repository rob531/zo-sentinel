"""logic.py -- data/computation layer for risk_tier_change_analysis."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


def get_current_risk_tier(db: Session, server_id: str) -> Optional[str]:
    """Get the current risk_tier for a server from the registry."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    return server.risk_tier if server else None


def get_historical_axis_scores(
    db: Session,
    server_id: str,
    axis_name: str = "overall_risk",
    days_back: int = 30,
) -> List[McpLlmAxisScore]:
    """Get historical axis scores for a server within the lookback window."""
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    rows = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name,
        McpLlmAxisScore.scored_at >= cutoff,
    ).order_by(desc(McpLlmAxisScore.scored_at)).all()
    return rows


def compute_tier_change(
    db: Session,
    server_id: str,
    days_back: int = 30,
) -> Optional[Dict]:
    """
    Detect risk tier changes for a server by comparing current registry tier
    against historical axis scores within the lookback window.
    """
    current_tier = get_current_risk_tier(db, server_id)
    if not current_tier:
        return None

    historical = get_historical_axis_scores(db, server_id, days_back=days_back)
    if not historical:
        return {
            "server_id": server_id,
            "current_tier": current_tier,
            "previous_tier": None,
            "changed": False,
            "change_direction": None,
        }

    # Find the oldest recorded tier (first in desc order = newest, last = oldest)
    oldest_record = historical[-1]
    previous_tier = oldest_record.label

    if previous_tier is None:
        return {
            "server_id": server_id,
            "current_tier": current_tier,
            "previous_tier": None,
            "changed": False,
            "change_direction": None,
        }

    tier_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
    prev_val = tier_order.get(previous_tier, 99)
    curr_val = tier_order.get(current_tier, 99)

    if prev_val < curr_val:
        direction = "escalated"
    elif prev_val > curr_val:
        direction = "de_escalated"
    else:
        direction = None

    return {
        "server_id": server_id,
        "current_tier": current_tier,
        "previous_tier": previous_tier,
        "changed": direction is not None,
        "change_direction": direction,
        "historical_record_count": len(historical),
    }


def get_all_tier_changes(db: Session, days_back: int = 30) -> List[Dict]:
    """Compute tier changes for all servers with risk_tier set."""
    servers = db.query(McpServerRegistry).filter(
        McpServerRegistry.risk_tier.isnot(None)
    ).all()

    changes = []
    for server in servers:
        change = compute_tier_change(db, server.server_id, days_back=days_back)
        if change:
            changes.append(change)
    return changes


def get_mesh_signal_scores(server_ids: List[str]) -> List[Dict]:
    """Fetch signal scores from the mesh store via write_service."""
    if not server_ids:
        return []
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "sql": "SELECT server_id, signal_name, score FROM mcp_signal_scores WHERE server_id IN (%s)" % ",".join(["?"] * len(server_ids)),
                "params": server_ids,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("rows", [])
    except Exception:
        pass
    return []
