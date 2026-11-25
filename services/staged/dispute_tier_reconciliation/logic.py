"""Logic for dispute tier reconciliation over the authoritative application data."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from app.db import get_session  # noqa: F401
from app.models import McpScoreDispute, McpServerRegistry
from sqlalchemy import select
from sqlalchemy.orm import Session


TIER_LADDER = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
)
_TIER_INDEX = {tier: index for index, tier in enumerate(TIER_LADDER)}
_UNKNOWN_TIER_INDEX = len(TIER_LADDER)


def _tier_index(tier: Optional[str]) -> int:
    """Return a tier's ladder position, treating missing tiers as worst-side."""
    if tier is None:
        return _UNKNOWN_TIER_INDEX
    return _TIER_INDEX.get(str(tier).strip().upper(), _UNKNOWN_TIER_INDEX)


def tier_delta(
    proposed_tier: Optional[str], current_tier: Optional[str]
) -> int:
    """Return the signed distance from the current tier to the proposed tier."""
    return _tier_index(proposed_tier) - _tier_index(current_tier)


def _age_days(created_at: Optional[datetime], now: datetime) -> int:
    if created_at is None:
        return 0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0, (now - created_at).days)


def get_tier_reconciliation(
    db: Session, status: Optional[str] = None
) -> Dict[str, object]:
    """Build the tier-reconciliation report from disputes and the server registry."""
    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    )
    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    rows = db.execute(statement).all()
    now = datetime.now(timezone.utc)

    items = []
    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    agree_count = 0
    escalate_count = 0
    deescalate_count = 0
    unknown_tier_count = 0
    stale_count = 0

    for row in rows:
        (
            dispute_id,
            server_id,
            proposed_overall_risk,
            reason_category,
            row_status,
            created_at,
            server_name,
            risk_tier,
        ) = row

        proposed_tier = proposed_overall_risk
        current_tier = risk_tier or "UNKNOWN"
        delta = tier_delta(proposed_tier, risk_tier)
        age_days = _age_days(created_at, now)
        is_stale = age_days > 30

        status_key = row_status if row_status is not None else "UNKNOWN"
        reason_key = (
            reason_category if reason_category is not None else "UNKNOWN"
        )
        by_status[status_key] = by_status.get(status_key, 0) + 1
        by_reason_category[reason_key] = by_reason_category.get(reason_key, 0) + 1

        if delta == 0:
            agree_count += 1
        elif delta > 0:
            escalate_count += 1
        else:
            deescalate_count += 1

        if risk_tier is None:
            unknown_tier_count += 1
        if is_stale:
            stale_count += 1

        items.append(
            {
                "dispute_id": dispute_id,
                "server_id": server_id,
                "server_name": server_name or "",
                "status": row_status,
                "reason_category": reason_category,
                "proposed_tier": proposed_tier,
                "current_tier": current_tier,
                "delta": delta,
                "age_days": age_days,
                "is_stale": is_stale,
            }
        )

    items.sort(key=lambda item: item["age_days"], reverse=True)

    return {
        "total": len(items),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "agree_count": agree_count,
        "escalate_count": escalate_count,
        "deescalate_count": deescalate_count,
        "unknown_tier_count": unknown_tier_count,
        "stale_count": stale_count,
        "items": items,
    }


def build_tier_reconciliation(
    db: Session, status: Optional[str] = None
) -> Dict[str, object]:
    """Compatibility alias for the reconciliation query."""
    return get_tier_reconciliation(db, status=status)


def reconcile_disputes(
    db: Session, status: Optional[str] = None
) -> Dict[str, object]:
    """Compatibility alias for the reconciliation query."""
    return get_tier_reconciliation(db, status=status)


if __name__ == "__main__":
    assert tier_delta("TRUSTED_GENERAL", "TRUSTED_GENERAL") == 0
    assert tier_delta("ENTERPRISE_CONTROLLED", "TRUSTED_GENERAL") > 0
    assert tier_delta("TRUSTED_GENERAL", "ENTERPRISE_CONTROLLED") < 0
    assert tier_delta("TRUSTED_GENERAL", None) < 0
    print("PASS")