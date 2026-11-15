"""logic.py – data/computation layer for the dispute_tier_diff service.

Provides a single pure function that, given a SQLAlchemy Session, returns a
summary of score disputes together with the tier implied by the stored axis
scores and a flag indicating whether the analyst‑proposed tier diverges from
the current tier.

The function is used by the router to serve GET /api/disputes/tier-diff.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpScoreDispute


def _determine_current_tier(
    axis_rows: List[tuple[str, Optional[float]]],
) -> Optional[str]:
    """Return the axis name with the highest p_top, or None if no rows."""
    if not axis_rows:
        return None
    # Filter out rows where p_top is None
    filtered = [(name, p) for name, p in axis_rows if p is not None]
    if not filtered:
        return None
    # Choose the axis with the maximum p_top
    return max(filtered, key=lambda item: item[1])[0]


def get_dispute_tier_diff(
    db: Session, *, status: str = "open"
) -> Dict[str, Any]:
    """
    Compute tier differences for disputes.

    Parameters
    ----------
    db: Session
        Active SQLAlchemy session.
    status: str, optional
        Dispute status filter (default ``"open"``).

    Returns
    -------
    dict
        ``{"count": int, "disputes": [ ... ]}`` where each dispute dict contains
        the fields required by the API contract.
    """
    # Fetch disputes matching the status filter
    dispute_rows = db.execute(
        select(McpScoreDispute)
        .where(McpScoreDispute.status == status)
    ).scalars().all()

    disputes_out: List[Dict[str, Any]] = []

    for dispute in dispute_rows:
        # Load axis scores for the server referenced by the dispute
        axis_rows = db.execute(
            select(McpLlmAxisScore.axis_name, McpLlmAxisScore.p_top)
            .where(McpLlmAxisScore.server_id == dispute.server_id)
        ).all()

        axis_deltas = [
            {"axis_name": axis_name, "p_top": p_top}
            for axis_name, p_top in axis_rows
        ]

        current_tier = _determine_current_tier(axis_rows)
        proposed_tier = dispute.proposed_overall_risk

        divergent = (
            current_tier is not None
            and proposed_tier is not None
            and current_tier != proposed_tier
        )

        disputes_out.append(
            {
                "id": dispute.id,
                "server_id": dispute.server_id,
                "status": dispute.status,
                "reason_category": dispute.reason_category,
                "proposed_tier": proposed_tier,
                "current_tier": current_tier,
                "divergent": divergent,
                "axis_deltas": axis_deltas,
                "created_at": dispute.created_at.replace(tzinfo=timezone.utc).isoformat(),
            }
        )

    return {"count": len(disputes_out), "disputes": disputes_out}


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Local in‑memory SQLite for the self‑test; we still import the real models.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as session:
        # Seed two disputes
        dispute1 = McpScoreDispute(
            id=1,
            server_id="srv1",
            status="open",
            reason_category="test",
            proposed_overall_risk="high",
            explanation="ex1",
            proposed_axes="{}",
            submitted_by="tester",
        )
        dispute2 = McpScoreDispute(
            id=2,
            server_id="srv2",
            status="open",
            reason_category="test2",
            proposed_overall_risk="A",
            explanation="ex2",
            proposed_axes="{}",
            submitted_by="tester",
        )
        session.add_all([dispute1, dispute2])

        # Seed axis scores for srv1 (divergent)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="srv1",
                    axis_name="A",
                    p_top=0.9,
                    model_version="v1",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv1",
                    axis_name="B",
                    p_top=0.1,
                    model_version="v1",
                ),
            ]
        )
        # Seed axis scores for srv2 (matching)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=3,
                    server_id="srv2",
                    axis_name="A",
                    p_top=0.8,
                    model_version="v1",
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="srv2",
                    axis_name="B",
                    p_top=0.2,
                    model_version="v1",
                ),
            ]
        )
        session.commit()

        result = get_dispute_tier_diff(session)

        # Acceptance checks
        assert result["count"] == 2, "expected two disputes"
        divergent_cnt = sum(1 for d in result["disputes"] if d["divergent"])
        assert divergent_cnt == 1, "expected exactly one divergent dispute"

        for d in result["disputes"]:
            if d["divergent"]:
                assert d["proposed_tier"] is not None, "proposed_tier must be set"
                assert d["current_tier"] is not None, "current_tier must be set"

        print("PASS")