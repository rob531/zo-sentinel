"""Logic for the `score_disputes_detail` staged service.

Provides a function that returns dispute records for a given server id,
ordered by creation time descending.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, List

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Real application imports – must not be stubbed.
from app.db import Base, get_session
from app.models import McpScoreDispute


def get_score_disputes_detail(
    server_id: str, db: Session = Depends(get_session)
) -> List[dict[str, Any]]:
    """Return dispute metadata for *server_id* ordered by newest first.

    The returned dictionaries contain the fields required by the router
    (`services/staged/score_disputes_detail/router.py`).

    Args:
        server_id: The identifier of the server whose disputes are requested.
        db: SQLAlchemy session injected by FastAPI.

    Returns:
        A list of dictionaries, each representing a dispute.
    """
    rows = (
        db.query(McpScoreDispute)
        .filter(McpScoreDispute.server_id == server_id)
        .order_by(McpScoreDispute.created_at.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "submitted_by": row.submitted_by,
            "proposed_overall_risk": row.proposed_overall_risk,
            "proposed_axes": row.proposed_axes,
            "reason_category": row.reason_category,
            "explanation": row.explanation,
            "status": row.status,
            "admin_note": row.admin_note,
            "created_at": row.created_at,
            "resolved_at": row.resolved_at,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # Create an in‑memory SQLite engine and initialise the real metadata.
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    # Helper to provide a session that mimics the FastAPI dependency.
    def _get_test_session() -> Session:
        db = _SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Insert three test disputes.
    test_server_id = "test-srv-001"
    now = datetime.datetime.utcnow()
    disputes = [
        McpScoreDispute(
            server_id=test_server_id,
            submitted_by="alice@example.com",
            proposed_overall_risk=0.42,
            proposed_axes=json.dumps({"confidentiality": 0.7, "integrity": 0.3}),
            reason_category="policy",
            explanation="Initial test dispute",
            status="open",
            admin_note="",
            created_at=now - datetime.timedelta(minutes=2),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=test_server_id,
            submitted_by="bob@example.com",
            proposed_overall_risk=0.85,
            proposed_axes=json.dumps({"availability": 0.9}),
            reason_category="risk",
            explanation="Second test dispute",
            status="closed",
            admin_note="Reviewed",
            created_at=now - datetime.timedelta(minutes=1),
            resolved_at=now,
        ),
        McpScoreDispute(
            server_id=test_server_id,
            submitted_by="carol@example.com",
            proposed_overall_risk=0.15,
            proposed_axes=json.dumps({"confidentiality": 0.2}),
            reason_category="false_positive",
            explanation="Third test dispute",
            status="open",
            admin_note="",
            created_at=now,
            resolved_at=None,
        ),
    ]

    # Persist test data.
    with _SessionLocal() as db:
        db.add_all(disputes)
        db.commit()

    # Run the logic function directly (bypassing FastAPI's Depends).
    with _SessionLocal() as db:
        result = get_score_disputes_detail(test_server_id, db=db)

    # Assertions per the acceptance criteria.
    assert isinstance(result, list), "Result should be a list"
    assert len(result) == 3, f"Expected 3 disputes, got {len(result)}"
    # The first item should be the most recent (created_at == now)
    first = result[0]
    assert first["status"] == "open", f"Unexpected status: {first['status']}"
    assert first["submitted_by"] == "carol@example.com"
    assert first["created_at"] == now

    print("PASS")