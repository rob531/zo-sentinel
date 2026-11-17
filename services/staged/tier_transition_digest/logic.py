"""logic.py -- tier transition digest computation over the real application data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
CAUTION_LIMITED = "CAUTION_LIMITED"
ENTERPRISE_CONTROLLED = "ENTERPRISE_CONTROLLED"
TRUSTED_RESEARCH = "TRUSTED_RESEARCH"
TRUSTED_GENERAL = "TRUSTED_GENERAL"


def tier_for_p_top(p_top: Optional[float]) -> str:
    """Return the dashboard tier corresponding to an overall-risk probability."""
    if p_top is None:
        return TRUSTED_GENERAL
    value = float(p_top)
    if value >= 0.80:
        return HIGH_RISK_ISOLATED
    if value >= 0.60:
        return CAUTION_LIMITED
    if value >= 0.40:
        return ENTERPRISE_CONTROLLED
    if value >= 0.20:
        return TRUSTED_RESEARCH
    return TRUSTED_GENERAL


# Keep the threshold conversion available under the name used by adjacent
# dashboard services.
derive_tier = tier_for_p_top


def _utc_date(value: Any) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date()
    return datetime.now(timezone.utc).date()


def _as_utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_tier_transition_digest(
    db: Session,
    days: int = 14,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the read-only day-over-day tier transition digest.

    Scores are ordered independently for each server.  A transition is
    counted when the derived tier of a score differs from the previous score's
    derived tier for that server.  The current mix uses each server's latest
    derived tier; registry-only servers retain their registry tier.
    """
    if days < 1:
        days = 1
    if days > 90:
        days = 90

    reference = _as_utc(as_of)
    cutoff = reference.date() - timedelta(days=days)

    registry_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
    ).all()
    registry_tiers: Dict[str, str] = {}
    for server_id, risk_tier in registry_rows:
        if server_id is not None and risk_tier:
            registry_tiers[str(server_id)] = str(risk_tier)

    score_rows = db.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at)
    ).scalars().all()

    by_server: Dict[str, List[Tuple[datetime, str]]] = {}
    for score in score_rows:
        if score.server_id is None:
            continue
        scored_at = _as_utc(score.scored_at)
        by_server.setdefault(str(score.server_id), []).append(
            (scored_at, tier_for_p_top(score.p_top))
        )

    transitions: Dict[Tuple[date, str, str], int] = {}
    current_mix: Dict[str, int] = {}

    for server_id, scores in by_server.items():
        previous_tier: Optional[str] = None
        latest_tier: Optional[str] = None
        for scored_at, tier in scores:
            if previous_tier is not None and tier != previous_tier:
                transition_date = scored_at.date()
                if cutoff <= transition_date <= reference.date():
                    key = (transition_date, previous_tier, tier)
                    transitions[key] = transitions.get(key, 0) + 1
            previous_tier = tier
            latest_tier = tier

        if latest_tier is not None:
            current_mix[latest_tier] = current_mix.get(latest_tier, 0) + 1

    for server_id, tier in registry_tiers.items():
        if server_id not in by_server:
            current_mix[tier] = current_mix.get(tier, 0) + 1

    transition_items: List[Dict[str, Any]] = []
    for (transition_date, from_tier, to_tier), count in sorted(
        transitions.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2]),
    ):
        transition_items.append(
            {
                "date": transition_date.isoformat(),
                "from_tier": from_tier,
                "to_tier": to_tier,
                "count": int(count),
            }
        )

    return {
        "as_of": reference.isoformat(),
        "transitions": transition_items,
        "current_mix": current_mix,
        "transition_total": sum(int(item["count"]) for item in transition_items),
    }


# Compatibility names for routers and service contracts.
build_digest = build_tier_transition_digest
compute_tier_transition_digest = build_tier_transition_digest
get_tier_transition_digest = build_tier_transition_digest


if __name__ == "__main__":
    assert tier_for_p_top(0.10) == TRUSTED_GENERAL
    assert tier_for_p_top(0.20) == TRUSTED_RESEARCH
    assert tier_for_p_top(0.40) == ENTERPRISE_CONTROLLED
    assert tier_for_p_top(0.60) == CAUTION_LIMITED
    assert tier_for_p_top(0.80) == HIGH_RISK_ISOLATED
    print("PASS")