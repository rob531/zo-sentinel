# services/staged/score_tier_scorer/logic.py
from datetime import datetime
from typing import List, Dict

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class ServerTierScore(BaseModel):
    server_id: str
    tier: str
    score: float
    scored_at: datetime


class TierScoreResponse(BaseModel):
    servers: List[ServerTierScore]


# ----------------------------------------------------------------------
# Core computation
# ----------------------------------------------------------------------
def _map_average_to_tier(avg: float) -> str:
    """Map an average `p_top` score to a risk tier."""
    if avg >= 0.8:
        return "HIGH_RISK"
    if avg >= 0.5:
        return "MEDIUM_RISK"
    return "LOW_RISK"


def compute_tier_scores(db: Session) -> TierScoreResponse:
    """
    Compute a composite risk tier per server.

    - Join axis scores with server registry.
    - If any axis for a server has `p_critical > 0.7`,
      the tier is forced to ``HIGH_RISK_ISOLATED``.
    - Otherwise the tier is derived from the average `p_top`
      across all axes for that server.
    - The returned ``score`` is the average `p_top` (or 1.0
      when the override tier is applied).
    - ``scored_at`` is the most recent timestamp among the
      server's axis scores.
    """
    # Gather all needed rows in a single query
    rows = (
        db.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.scored_at,
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .all()
    )

    # Organise rows per server
    servers: Dict[str, Dict] = {}
    for server_id, p_top, p_critical, scored_at in rows:
        if server_id not in servers:
            servers[server_id] = {
                "p_top_sum": 0.0,
                "count": 0,
                "max_scored_at": scored_at,
                "critical_override": False,
            }
        srv = servers[server_id]
        srv["p_top_sum"] += float(p_top or 0)
        srv["count"] += 1
        if scored_at and scored_at > srv["max_scored_at"]:
            srv["max_scored_at"] = scored_at
        if p_critical is not None and float(p_critical) > 0.7:
            srv["critical_override"] = True

    # Build response objects
    result = []
    for server_id, data in servers.items():
        if data["critical_override"]:
            tier = "HIGH_RISK_ISOLATED"
            score = 1.0
        else:
            avg = data["p_top_sum"] / max(data["count"], 1)
            tier = _map_average_to_tier(avg)
            score = avg
        result.append(
            ServerTierScore(
                server_id=server_id,
                tier=tier,
                score=round(score, 4),
                scored_at=data["max_scored_at"],
            )
        )

    # Sort for deterministic output
    result.sort(key=lambda x: x.server_id)
    return TierScoreResponse(servers=result)


# ----------------------------------------------------------------------
# FastAPI dependency wrapper (used by routers)
# ----------------------------------------------------------------------
def get_tier_scores(db: Session = Depends(get_session)) -> TierScoreResponse:
    """FastAPI endpoint helper – returns tier scores using the app DB session."""
    return compute_tier_scores(db)


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------
    # In‑memory SQLite setup (mirrors real models)
    # ------------------------------------------------------------------
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    test_db = SessionLocal()

    # ------------------------------------------------------------------
    # Seed data: three servers with mixed axis scores
    # ------------------------------------------------------------------
    now = datetime.utcnow()

    # Server 1 – should trigger HIGH_RISK_ISOLATED (p_critical > 0.7)
    test_db.add(
        McpServerRegistry(server_id="s1", risk_tier="UNKNOWN")
    )
    test_db.add_all(
        [
            McpLlmAxisScore(
                server_id="s1",
                axis_name="overall_risk",
                p_top=0.2,
                p_critical=0.8,  # triggers override
                p_danger=0.1,
                scored_at=now,
                label="low",
                label_index=0,
                adapter_sha256="a",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=1,
                model_version="m1",
                probs=None,
            ),
            McpLlmAxisScore(
                server_id="s1",
                axis_name="auth_strength",
                p_top=0.3,
                p_critical=0.1,
                p_danger=0.2,
                scored_at=now,
                label="low",
                label_index=0,
                adapter_sha256="b",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=2,
                model_version="m1",
                probs=None,
            ),
        ]
    )

    # Server 2 – average p_top high → HIGH_RISK
    test_db.add(
        McpServerRegistry(server_id="s2", risk_tier="UNKNOWN")
    )
    test_db.add_all(
        [
            McpLlmAxisScore(
                server_id="s2",
                axis_name="overall_risk",
                p_top=0.9,
                p_critical=0.2,
                p_danger=0.1,
                scored_at=now,
                label="high",
                label_index=2,
                adapter_sha256="c",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=3,
                model_version="m1",
                probs=None,
            ),
            McpLlmAxisScore(
                server_id="s2",
                axis_name="auth_strength",
                p_top=0.85,
                p_critical=0.1,
                p_danger=0.05,
                scored_at=now,
                label="high",
                label_index=2,
                adapter_sha256="d",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=4,
                model_version="m1",
                probs=None,
            ),
        ]
    )

    # Server 3 – low average p_top → LOW_RISK
    test_db.add(
        McpServerRegistry(server_id="s3", risk_tier="UNKNOWN")
    )
    test_db.add_all(
        [
            McpLlmAxisScore(
                server_id="s3",
                axis_name="overall_risk",
                p_top=0.3,
                p_critical=0.1,
                p_danger=0.2,
                scored_at=now,
                label="low",
                label_index=0,
                adapter_sha256="e",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=5,
                model_version="m1",
                probs=None,
            ),
            McpLlmAxisScore(
                server_id="s3",
                axis_name="auth_strength",
                p_top=0.4,
                p_critical=0.05,
                p_danger=0.1,
                scored_at=now,
                label="low",
                label_index=0,
                adapter_sha256="f",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=6,
                model_version="m1",
                probs=None,
            ),
        ]
    )

    test_db.commit()

    # ------------------------------------------------------------------
    # Execute logic and validate expectations
    # ------------------------------------------------------------------
    response = compute_tier_scores(test_db)

    # Build a quick lookup
    tiers = {s.server_id: s.tier for s in response.servers}

    assert tiers["s1"] == "HIGH_RISK_ISOLATED", "s1 tier mismatch"
    assert tiers["s2"] == "HIGH_RISK", "s2 tier mismatch"
    assert tiers["s3"] == "LOW_RISK", "s3 tier mismatch"

    print("PASS")