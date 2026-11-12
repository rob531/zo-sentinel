"""logic.py -- data/computation layer for the dispute_status_board service.

Provides a single pure function that aggregates dispute information from the
real `McpScoreDispute` table using the application's SQLAlchemy session.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def dispute_status_board(
    db: Session,
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return aggregated dispute information.

    Parameters
    ----------
    db: Session
        Active SQLAlchemy session.
    status: Optional[str]
        If provided, restrict rows to this status before aggregation.
    limit: int
        Echoed back in the response; does not affect aggregation.

    Returns
    -------
    dict
        {
            "total": int,
            "by_status": {"open": int, "resolved": int, ...},
            "by_reason_category": [{"reason_category": str, "count": int}, ...],
            "oldest_open_age_days": float,
            "limit": int,
        }
    """
    # Base selectable with optional status filter
    base_stmt = select(McpScoreDispute)
    if status is not None:
        base_stmt = base_stmt.where(McpScoreDispute.status == status)

    # Total count (after optional filter)
    total_stmt = select(func.count()).select_from(McpScoreDispute)
    if status is not None:
        total_stmt = total_stmt.where(McpScoreDispute.status == status)
    total = db.execute(total_stmt).scalar_one()

    # Count by status
    by_status_stmt = (
        select(McpScoreDispute.status, func.count())
        .group_by(McpScoreDispute.status)
    )
    if status is not None:
        by_status_stmt = by_status_stmt.where(McpScoreDispute.status == status)
    rows = db.execute(by_status_stmt).all()
    by_status: Dict[str, int] = {}
    for st, cnt in rows:
        by_status[st or "unknown"] = int(cnt)

    # Count by reason_category
    by_reason_stmt = (
        select(McpScoreDispute.reason_category, func.count())
        .group_by(McpScoreDispute.reason_category)
    )
    if status is not None:
        by_reason_stmt = by_reason_stmt.where(McpScoreDispute.status == status)
    reason_rows = db.execute(by_reason_stmt).all()
    by_reason_category: List[Dict[str, Any]] = [
        {"reason_category": rc or "unknown", "count": int(cnt)}
        for rc, cnt in reason_rows
    ]

    # Oldest unresolved dispute age (in days)
    # Unresolved = status not 'resolved' (and resolved_at is NULL)
    oldest_stmt = (
        select(func.min(McpScoreDispute.created_at))
        .where(McpScoreDispute.status != "resolved")
    )
    oldest_created = db.execute(oldest_stmt).scalar_one_or_none()
    if oldest_created is None:
        oldest_open_age_days = 0.0
    else:
        now = _dt.datetime.utcnow()
        delta = now - oldest_created
        oldest_open_age_days = delta.total_seconds() / 86_400.0

    return {
        "total": int(total),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "oldest_open_age_days": float(oldest_open_age_days),
        "limit": limit,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and initialise tables using the real models
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.db import Base  # type: ignore  # Base is defined in app.db

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = _dt.datetime.utcnow()

    with SessionLocal() as db:
        # Seed three disputes
        dispute_open = McpScoreDispute(
            id=1,
            server_id=101,
            submitted_by=1,
            proposed_overall_risk=5,
            proposed_axes=None,
            reason_category="axis_disagreement",
            explanation="Disagreement on axis weighting",
            status="open",
            admin_note=None,
            created_at=now - _dt.timedelta(days=5),
            resolved_at=None,
        )
        dispute_resolved = McpScoreDispute(
            id=2,
            server_id=102,
            submitted_by=2,
            proposed_overall_risk=3,
            proposed_axes=None,
            reason_category="data_error",
            explanation="Incorrect data source",
            status="resolved",
            admin_note=None,
            created_at=now - _dt.timedelta(days=10),
            resolved_at=now - _dt.timedelta(days=2),
        )
        dispute_escalated = McpScoreDispute(
            id=3,
            server_id=103,
            submitted_by=3,
            proposed_overall_risk=7,
            proposed_axes=None,
            reason_category="policy_violation",
            explanation="Policy breach",
            status="escalated",
            admin_note=None,
            created_at=now - _dt.timedelta(days=3),
            resolved_at=None,
        )
        db.add_all([dispute_open, dispute_resolved, dispute_escalated])
        db.commit()

        # Run the logic
        result = dispute_status_board(db)

        # Assertions per acceptance criteria
        assert result["total"] == 3, "total count mismatch"
        assert result["by_status"].get("open", 0) == 1, "open status count mismatch"
        reason_counts = {r["reason_category"]: r["count"] for r in result["by_reason_category"]}
        assert reason_counts.get("axis_disagreement", 0) == 1, "reason_category count mismatch"

        print("PASS")