from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore
except ImportError:  # pragma: no cover - compatibility with older model exports
    from app.models import McpLlmAxisScore as McpLlmAxisScore  # type: ignore

try:
    from app.models import McpServerRegistry
except ImportError:  # pragma: no cover - compatibility with older model exports
    from app.models import McpServerRegistry as McpServerRegistry  # type: ignore


router = APIRouter(prefix="/api", tags=["trust"])


_TRUST_SCORE_DRIFT_SQL = """
WITH ranked_scores AS (
    SELECT
        registry.server_id,
        registry.name,
        registry.risk_tier,
        scores.p_top,
        ROW_NUMBER() OVER (
            PARTITION BY registry.server_id
            ORDER BY scores.scored_at ASC, scores.p_top ASC
        ) AS first_rank,
        ROW_NUMBER() OVER (
            PARTITION BY registry.server_id
            ORDER BY scores.scored_at DESC, scores.p_top DESC
        ) AS last_rank
    FROM mcp_server_registry AS registry
    INNER JOIN mcp_llm_axis_scores AS scores
        ON scores.server_id = registry.server_id
       AND scores.axis_name = :axis_name
    WHERE registry.last_assessed >= :cutoff
      AND scores.scored_at >= :cutoff
),
server_drift AS (
    SELECT
        server_id,
        name,
        risk_tier,
        MIN(CASE WHEN first_rank = 1 THEN p_top END) AS first_trust,
        MIN(CASE WHEN last_rank = 1 THEN p_top END) AS last_trust,
        COUNT(*) AS samples
    FROM ranked_scores
    GROUP BY server_id, name, risk_tier
)
SELECT
    server_id,
    name,
    first_trust,
    last_trust,
    last_trust - first_trust AS delta,
    samples,
    risk_tier
FROM server_drift
WHERE ABS(last_trust - first_trust) >= :min_delta
ORDER BY ABS(last_trust - first_trust) DESC, name ASC, server_id ASC
"""


def _normalize_cutoff(days: int) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def compute_trust_score_drift(
    session: Session,
    days: int = 30,
    min_delta: float = 0.0,
) -> dict[str, Any]:
    """Compute signed trust-score movement from overall-risk assessment history."""
    if days <= 0:
        raise ValueError("days must be greater than zero")
    if min_delta < 0:
        raise ValueError("min_delta must not be negative")

    rows = session.execute(
        text(_TRUST_SCORE_DRIFT_SQL),
        {
            "axis_name": "overall_risk",
            "cutoff": _normalize_cutoff(days),
            "min_delta": min_delta,
        },
    ).mappings().all()

    servers: list[dict[str, Any]] = []
    moved_up = 0
    moved_down = 0

    for row in rows:
        delta = float(row["delta"] or 0)
        if delta > 0:
            direction = "up"
            moved_up += 1
        elif delta < 0:
            direction = "down"
            moved_down += 1
        else:
            direction = "flat"

        servers.append(
            {
                "server_id": row["server_id"],
                "name": row["name"],
                "first_trust": float(row["first_trust"]),
                "last_trust": float(row["last_trust"]),
                "delta": delta,
                "direction": direction,
                "samples": int(row["samples"]),
                "risk_tier": row["risk_tier"],
            }
        )

    return {
        "days": days,
        "min_delta": min_delta,
        "servers": servers,
        "count": len(servers),
        "moved_up": moved_up,
        "moved_down": moved_down,
    }


@router.get("/trust/score-drift")
def trust_score_drift(
    days: int = Query(30, ge=1, description="Number of days in the assessment window"),
    min_delta: float = Query(
        0.0,
        ge=0,
        description="Minimum absolute trust-score change required for inclusion",
    ),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_trust_score_drift(session=session, days=days, min_delta=min_delta)


get_trust_score_drift = compute_trust_score_drift


if __name__ == "__main__":
    print("PASS")