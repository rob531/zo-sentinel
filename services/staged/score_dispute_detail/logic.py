"""logic.py -- data/computation layer for the score_dispute_detail service.

Provides a pure function that fetches a score dispute and the latest axis
scores for its server.  The function raises HTTPException(404) when the
dispute does not exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import McpLlmAxisScore, McpScoreDispute


def get_score_dispute_detail(db: Session, dispute_id: int) -> Dict[str, Any]:
    """Return dispute details together with the newest axis scores.

    Args:
        db: SQLAlchemy Session.
        dispute_id: Primary key of the McpScoreDispute row.

    Returns:
        A dict matching the ScoreDisputeDetail contract.

    Raises:
        HTTPException: 404 if the dispute does not exist.
    """
    dispute = db.get(McpScoreDispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="Score dispute not found")

    # Subquery to get the latest scored_at per axis for this server
    latest_subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.server_id == dispute.server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    stmt = (
        select(McpLlmAxisScore)
        .join(
            latest_subq,
            (McpLlmAxisScore.axis_name == latest_subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == latest_subq.c.max_scored_at),
        )
        .where(McpLlmAxisScore.server_id == dispute.server_id)
    )
    rows: List[McpLlmAxisScore] = db.execute(stmt).scalars().all()

    axes: List[Dict[str, Any]] = []
    overall_risk: str | None = None
    for row in rows:
        axis_dict = {
            "axis_name": row.axis_name,
            "label": row.label,
            "p_top": row.p_top,
            "escalated": row.escalated,
            "escalated_to": row.escalated_to,
        }
        axes.append(axis_dict)
        if row.axis_name == "overall_risk":
            overall_risk = row.label

    dispute_dict = {
        "id": dispute.id,
        "server_id": dispute.server_id,
        "submitted_by": dispute.submitted_by,
        "proposed_overall_risk": dispute.proposed_overall_risk,
        "proposed_axes": dispute.proposed_axes,
        "reason_category": dispute.reason_category,
        "explanation": dispute.explanation,
        "status": dispute.status,
        "admin_note": dispute.admin_note,
        "created_at": dispute.created_at.isoformat()
        if isinstance(dispute.created_at, datetime)
        else None,
        "resolved_at": dispute.resolved_at.isoformat()
        if isinstance(dispute.resolved_at, datetime)
        else None,
    }

    return {
        "dispute": dispute_dict,
        "current_axes": axes,
        "current_overall_risk": overall_risk,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", future=True, echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    db = SessionLocal()

    # Seed data
    from datetime import timezone, timedelta

    dispute = McpScoreDispute(
        id=1,
        server_id="srv-123",
        submitted_by="alice",
        proposed_overall_risk="HIGH_RISK_ISOLATED",
        proposed_axes=[],
        reason_category="test_category",
        explanation="test explanation",
        status="pending",
        admin_note=None,
        created_at=datetime.now(timezone.utc),
        resolved_at=None,
    )
    db.add(dispute)

    score_overall = McpLlmAxisScore(
        id=1,
        server_id="srv-123",
        axis_name="overall_risk",
        label="HIGH_RISK_ISOLATED",
        label_index=0,
        probs={},
        p_top=0.92,
        p_critical=0.05,
        p_danger=0.03,
        escalated=False,
        escalated_to=None,
        decision_rule_version="v1",
        model_version="m1",
        scored_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    score_auth = McpLlmAxisScore(
        id=2,
        server_id="srv-123",
        axis_name="auth_strength",
        label="LOW",
        label_index=1,
        probs={},
        p_top=0.68,
        p_critical=0.20,
        p_danger=0.12,
        escalated=True,
        escalated_to="admin",
        decision_rule_version="v1",
        model_version="m1",
        scored_at=datetime.now(timezone.utc),
    )
    db.add_all([score_overall, score_auth])
    db.commit()

    # Successful fetch
    result = get_score_dispute_detail(db, 1)
    assert result["dispute"]["id"] == 1
    assert result["dispute"]["proposed_overall_risk"] == "HIGH_RISK_ISOLATED"
    assert len(result["current_axes"]) == 2
    assert any(ax["axis_name"] == "overall_risk" for ax in result["current_axes"])

    # Missing dispute
    try:
        get_score_dispute_detail(db, 999)
        raise AssertionError("Expected HTTPException for missing dispute")
    except HTTPException as exc:
        assert exc.status_code == 404

    print("PASS")