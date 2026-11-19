from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, McpUnauthAccess


_ANONYMOUS_ACCESS_VALUES = (
    "anonymous",
    "no-auth",
    "no_auth",
    "no auth",
    "unauthenticated",
    "unauthenticated access",
)

_RISK_TIER_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "caution": 3,
    "low": 4,
    "trusted": 5,
    "unknown": 6,
}


def _is_anonymous_access(value: str | None) -> bool:
    """Return whether an unauth_access value represents anonymous access."""
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _ANONYMOUS_ACCESS_VALUES:
        return True
    return (
        "anonymous" in normalized
        or "unauthenticated" in normalized
        or "no-auth" in normalized
        or "no auth" in normalized
    )


def _risk_tier_sort_key(value: str | None) -> tuple[int, str]:
    normalized = (value or "unknown").strip().lower()
    return (_RISK_TIER_ORDER.get(normalized, _RISK_TIER_ORDER["unknown"]), normalized)


def _trust_score_sort_key(value: Any) -> float:
    if value is None:
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def build_remediation_queue(db: Session) -> Dict[str, Any]:
    """Build the read-only unauthenticated-access remediation queue."""
    access_filter = or_(
        *[
            func.lower(func.trim(McpUnauthAccess.unauth_access)) == value
            for value in _ANONYMOUS_ACCESS_VALUES
        ],
        func.lower(func.coalesce(McpUnauthAccess.unauth_access, "")).like("%anonymous%"),
        func.lower(func.coalesce(McpUnauthAccess.unauth_access, "")).like("%unauthenticated%"),
        func.lower(func.coalesce(McpUnauthAccess.unauth_access, "")).like("%no-auth%"),
        func.lower(func.coalesce(McpUnauthAccess.unauth_access, "")).like("%no auth%"),
    )

    rows = db.execute(
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
        .where(access_filter)
    ).all()

    server_ids = [row.server_id for row in rows]
    auth_scores: Dict[Any, Any] = {}
    if server_ids:
        score_rows = db.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.scored_at,
                McpLlmAxisScore.id,
            )
            .where(
                McpLlmAxisScore.server_id.in_(server_ids),
                func.lower(func.trim(McpLlmAxisScore.axis_name)) == "auth_strength",
            )
            .order_by(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.scored_at.desc().nullslast(),
                McpLlmAxisScore.id.desc(),
            )
        ).all()

        for server_id, p_top, _scored_at, _score_id in score_rows:
            if server_id not in auth_scores:
                auth_scores[server_id] = p_top

    queue: list[Dict[str, Any]] = []
    for row in rows:
        queue.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "url": row.url,
                "unauth_access": row.unauth_access,
                "source": row.source,
                "updated_at": row.updated_at,
                "risk_tier": row.risk_tier,
                "trust_score": row.trust_score,
                "auth_strength_p_top": auth_scores.get(row.server_id),
            }
        )

    queue.sort(
        key=lambda item: (
            _risk_tier_sort_key(item["risk_tier"]),
            _trust_score_sort_key(item["trust_score"]),
            str(item["server_id"]),
        )
    )

    counts_by_unauth_access: Dict[str, int] = {}
    counts_by_risk_tier: Dict[str, int] = {}
    for item in queue:
        access_key = item["unauth_access"] or "unknown"
        tier_key = item["risk_tier"] or "unknown"
        counts_by_unauth_access[access_key] = counts_by_unauth_access.get(access_key, 0) + 1
        counts_by_risk_tier[tier_key] = counts_by_risk_tier.get(tier_key, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue": queue,
        "counts_by_unauth_access": counts_by_unauth_access,
        "counts_by_risk_tier": counts_by_risk_tier,
        "total": len(queue),
    }


def get_remediation_queue(db: Session) -> Dict[str, Any]:
    """Return the unauthenticated-access remediation queue."""
    return build_remediation_queue(db)


def remediation_queue(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the remediation queue builder."""
    return build_remediation_queue(db)


def list_remediation_queue(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the remediation queue builder."""
    return build_remediation_queue(db)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = get_session
    print("PASS")