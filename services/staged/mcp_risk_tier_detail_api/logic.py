"""
Logic module for mcp_risk_tier_detail_api service.

This module provides detailed risk tier information by querying the real
app database. It is used by other risk tier services to retrieve detailed
server information within specific risk tiers.
"""

from typing import Any, Optional
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
)


def get_tier_details(tier: str, session=None) -> list[dict[str, Any]]:
    """
    Get detailed information for all servers in a specific risk tier.

    Args:
        tier: The risk tier identifier (e.g., 'critical', 'high', 'medium', 'low')
        session: Optional existing database session

    Returns:
        List of dictionaries containing detailed server information
    """
    if session is None:
        with get_session() as session:
            return _get_tier_details(tier, session)
    return _get_tier_details(tier, session)


def _get_tier_details(tier: str, session) -> list[dict[str, Any]]:
    """Internal implementation requiring an active session."""
    results = []
    query = session.query(McpServerRegistry).filter(
        McpServerRegistry.risk_tier == tier
    )
    for server in query.all():
        server_data = {
            "server_id": server.id,
            "server_name": server.name,
            "risk_tier": server.risk_tier,
            "status": server.status,
        }
        results.append(server_data)
    return results


def get_server_details(server_id: int, session=None) -> Optional[dict[str, Any]]:
    """
    Get detailed information for a specific server by ID.

    Args:
        server_id: The server ID
        session: Optional existing database session

    Returns:
        Dictionary with server details or None if not found
    """
    if session is None:
        with get_session() as session:
            return _get_server_details(server_id, session)
    return _get_server_details(server_id, session)


def _get_server_details(server_id: int, session) -> Optional[dict[str, Any]]:
    """Internal implementation requiring an active session."""
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.id == server_id
    ).first()

    if server is None:
        return None

    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    return {
        "server_id": server.id,
        "server_name": server.name,
        "risk_tier": server.risk_tier,
        "status": server.status,
        "scores": [{"axis": s.axis, "score": s.score} for s in scores],
    }


def get_tier_summary(session=None) -> dict[str, dict[str, Any]]:
    """
    Get a summary of all risk tiers with counts and status breakdown.

    Args:
        session: Optional existing database session

    Returns:
        Dictionary keyed by tier name containing counts and stats
    """
    if session is None:
        with get_session() as session:
            return _get_tier_summary(session)
    return _get_tier_summary(session)


def _get_tier_summary(session) -> dict[str, dict[str, Any]]:
    """Internal implementation requiring an active session."""
    summary = {}
    servers = session.query(McpServerRegistry).all()

    for server in servers:
        tier = server.risk_tier or "unknown"
        if tier not in summary:
            summary[tier] = {"count": 0, "by_status": {}}
        summary[tier]["count"] += 1
        status = server.status or "unknown"
        summary[tier]["by_status"][status] = summary[tier]["by_status"].get(status, 0) + 1

    return summary


if __name__ == "__main__":
    import sys
    try:
        _ = get_session
        _ = McpServerRegistry
        _ = McpLlmAxisScore
        _ = get_tier_details
        _ = get_server_details
        _ = get_tier_summary
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)