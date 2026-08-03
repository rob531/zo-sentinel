import datetime
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry


def _latest_axis_scores(db: Session, server_id: str) -> List[McpLlmAxisScore]:
    """Return the most recent score for each axis of a given server."""
    subq = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    scores = (
        db.query(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.max_scored_at),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    return scores


def compute_tier(server_id: str, db: Session) -> Dict:
    """
    Compute the risk tier for a given server.

    Returns a dictionary matching the contract:
    {
        "server_id": str,
        "risk_tier": str,
        "decision_rule_version": str,
        "model_version": str,
        "scored_at": str,
        "axes_summary": {
            "critical_count": int,
            "danger_count": int,
            "top_count": int,
        },
    }
    """
    scores = _latest_axis_scores(db, server_id)

    # Fallback: if no scores, try registry fallback tier
    if not scores:
        reg = (
            db.query(McpServerRegistry.risk_tier)
            .filter(McpServerRegistry.server_id == server_id)
            .scalar()
        )
        tier = reg if reg else "INSUFFICIENT"
        now = datetime.datetime.utcnow().isoformat()
        return {
            "server_id": server_id,
            "risk_tier": tier,
            "decision_rule_version": "v1",
            "model_version": "1.0",
            "scored_at": now,
            "axes_summary": {
                "critical_count": 0,
                "danger_count": 0,
                "top_count": 0,
            },
        }

    # Summaries
    critical_count = sum(1 for s in scores if s.label == "CRITICAL")
    danger_count = sum(1 for s in scores if s.label == "DANGER")
    top_count = sum(1 for s in scores if s.label == "TOP")
    max_scored_at = max(s.scored_at for s in scores)

    # Tier determination
    if critical_count > 0:
        tier = "HIGH_RISK_ISOLATED"
    else:
        # mean of p_top across axes
        mean_p_top = sum(s.p_top for s in scores) / len(scores)
        if mean_p_top >= 75:
            tier = "TRUSTED_GENERAL"
        elif mean_p_top >= 60:
            tier = "TRUSTED_RESEARCH"
        elif mean_p_top >= 45:
            tier = "ENTERPRISE_CONTROLLED"
        elif mean_p_top >= 30:
            tier = "CAUTION_LIMITED"
        elif mean_p_top >= 15:
            tier = "HIGH_RISK_ISOLATED"
        else:
            tier = "KNOWN_THREAT"

    return {
        "server_id": server_id,
        "risk_tier": tier,
        "decision_rule_version": "v1",
        "model_version": "1.0",
        "scored_at": max_scored_at.isoformat(),
        "axes_summary": {
            "critical_count": critical_count,
            "danger_count": danger_count,
            "top_count": top_count,
        },
    }


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Helper to add a score
    def add_score(
        server_id: str,
        axis_name: str,
        p_critical: float,
        p_danger: float,
        p_top: float,
        label: str,
        label_index: int,
        scored_at: datetime.datetime,
    ):
        db.add(
            McpLlmAxisScore(
                server_id=server_id,
                axis_name=axis_name,
                p_critical=p_critical,
                p_danger=p_danger,
                p_top=p_top,
                label=label,
                label_index=label_index,
                scored_at=scored_at,
            )
        )

    now = datetime.datetime.utcnow()

    # Server with a CRITICAL label on one axis
    add_score(
        "srv_critical",
        "axis1",
        0.9,
        0.0,
        0.0,
        "CRITICAL",
        0,
        now,
    )
    # Server with all TOP labels and high p_top
    for i in range(7):
        add_score(
            "srv_top",
            f"axis{i}",
            0.0,
            0.0,
            0.85,
            "TOP",
            2,
            now,
        )
    # Server with no scores (fallback)
    db.add(McpServerRegistry(server_id="srv_missing", risk_tier="UNKNOWN"))

    db.commit()

    # Tests
    res_critical = compute_tier("srv_critical", db)
    assert res_critical["risk_tier"] == "HIGH_RISK_ISOLATED"

    res_top = compute_tier("srv_top", db)
    assert res_top["risk_tier"] == "TRUSTED_GENERAL"

    res_missing = compute_tier("srv_missing", db)
    assert res_missing["risk_tier"] == "INSUFFICIENT"

    print("PASS")