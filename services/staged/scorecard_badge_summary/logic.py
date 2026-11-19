"""logic.py -- scorecard badge summary data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


_BADGES = {
    "HIGH_RISK_ISOLATED": "POOR",
    "KNOWN_THREAT": "POOR",
    "CAUTION_LIMITED": "WATCH",
    "ENTERPRISE_CONTROLLED": "WATCH",
    "INSUFFICIENT": "WATCH",
    "TRUSTED_GENERAL": "GOOD",
    "TRUSTED_RESEARCH": "GOOD",
}


def _badge_for_tier(tier: Optional[str], has_score: bool) -> str:
    if not has_score:
        return "UNSCORED"
    if tier:
        return _BADGES.get(tier.strip().upper(), "WATCH")
    return "WATCH"


def get_scorecard_badges(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Return compact scorecard badges for registered MCP servers.

    The latest score row for each server supplies the displayed axis and
    probability. Servers without score rows are returned as UNSCORED.
    """
    limit = max(0, min(int(limit), 200))

    registry_rows = db.execute(
        select(McpServerRegistry).order_by(
            McpServerRegistry.registry_source.asc(),
            McpServerRegistry.server_id.asc(),
        )
    ).scalars().all()

    score_rows = db.execute(
        select(McpLlmAxisScore).order_by(
            McpLlmAxisScore.server_id.asc(),
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    ).scalars().all()

    latest_scores: Dict[str, McpLlmAxisScore] = {}
    for score in score_rows:
        if score.server_id not in latest_scores:
            latest_scores[score.server_id] = score

    badges = []
    for server in registry_rows:
        score = latest_scores.get(server.server_id)
        tier = server.risk_tier
        badges.append(
            {
                "server_id": server.server_id,
                "name": server.name,
                "registry_source": server.registry_source,
                "badge": _badge_for_tier(tier, score is not None),
                "tier": tier,
                "trust_score": server.trust_score,
                "top_axis": score.axis_name if score is not None else None,
                "p_top": score.p_top if score is not None else None,
                "scored_at": score.scored_at if score is not None else None,
            }
        )

    return {
        "badges": badges[:limit],
        "total": len(badges),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_badges(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Compatibility alias for the scorecard badge summary."""
    return get_scorecard_badges(db, limit)


def get_badge_summary(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Compatibility alias for the scorecard badge summary."""
    return get_scorecard_badges(db, limit)


def build_badge_summary(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Compatibility alias for the scorecard badge summary."""
    return get_scorecard_badges(db, limit)


if __name__ == "__main__":
    assert _badge_for_tier("TRUSTED_GENERAL", True) == "GOOD"
    assert _badge_for_tier("CAUTION_LIMITED", True) == "WATCH"
    assert _badge_for_tier("HIGH_RISK_ISOLATED", True) == "POOR"
    assert _badge_for_tier(None, False) == "UNSCORED"
    print("PASS")