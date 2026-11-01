"""logic.py -- verdict and risk-tier conflict detection for the registry."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


VERDICT_RISK_TIER_MAP: Mapping[str, str] = {
    "TRUSTED_GENERAL": "low",
    "REVIEW_REQUIRED": "medium",
    "HIGH_RISK_ISOLATED": "high",
}


def _is_conflict(verdict: str | None, risk_tier: str | None) -> bool:
    """Return whether a registry verdict disagrees with its risk tier."""
    if verdict is None or risk_tier is None:
        return False
    expected_tier = VERDICT_RISK_TIER_MAP.get(verdict.strip().upper())
    if expected_tier is None:
        return False
    return risk_tier.strip().lower() != expected_tier


def get_verdict_conflicts(db: Session, limit: int = 100) -> List[Dict[str, Any]]:
    """Return registry rows whose verdict conflicts with their risk tier."""
    if limit <= 0:
        return []

    conflict_conditions = []
    for verdict, expected_tier in VERDICT_RISK_TIER_MAP.items():
        conflict_conditions.append(
            and_(
                func.lower(McpServerRegistry.verdict) == verdict.lower(),
                func.lower(McpServerRegistry.risk_tier) != expected_tier,
            )
        )

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.confidence,
            McpServerRegistry.last_assessed,
        )
        .where(or_(*conflict_conditions))
        .order_by(McpServerRegistry.server_id)
        .limit(limit)
    ).all()

    return [
        {
            "server_id": server_id,
            "name": name,
            "verdict": verdict,
            "risk_tier": risk_tier,
            "confidence": confidence,
            "last_assessed": last_assessed,
        }
        for server_id, name, verdict, risk_tier, confidence, last_assessed in rows
    ]


def find_verdict_conflicts(db: Session, limit: int = 100) -> List[Dict[str, Any]]:
    """Compatibility alias for callers using the find_* naming convention."""
    return get_verdict_conflicts(db, limit)


if __name__ == "__main__":
    assert _is_conflict("TRUSTED_GENERAL", "high")
    assert _is_conflict("HIGH_RISK_ISOLATED", "low")
    assert not _is_conflict("TRUSTED_GENERAL", "low")
    assert not _is_conflict("HIGH_RISK_ISOLATED", "high")
    assert not _is_conflict(None, "high")
    print("PASS")