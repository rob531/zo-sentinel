"""Data and computation layer for the score dispute queue."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


DEFAULT_DISPUTE_LIMIT = 50
MIN_DISPUTE_LIMIT = 1
MAX_DISPUTE_LIMIT = 200


def get_disputes(
    db: Session,
    status: Optional[str] = None,
    limit: int = DEFAULT_DISPUTE_LIMIT,
) -> Dict[str, Any]:
    """Return the analyst dispute queue and status counts."""
    bounded_limit = max(MIN_DISPUTE_LIMIT, min(MAX_DISPUTE_LIMIT, int(limit)))

    count_query = select(
        McpScoreDispute.status,
        func.count(McpScoreDispute.id),
    ).group_by(McpScoreDispute.status)
    if status is not None:
        count_query = count_query.where(McpScoreDispute.status == status)

    counts_by_status = {
        str(row_status): int(count)
        for row_status, count in db.execute(count_query).all()
    }

    disputes_query = select(McpScoreDispute)
    if status is not None:
        disputes_query = disputes_query.where(McpScoreDispute.status == status)
    disputes_query = disputes_query.order_by(desc(McpScoreDispute.created_at)).limit(
        bounded_limit
    )

    disputes = [
        {
            "id": dispute.id,
            "server_id": dispute.server_id,
            "submitted_by": dispute.submitted_by,
            "proposed_overall_risk": dispute.proposed_overall_risk,
            "proposed_axes": dispute.proposed_axes,
            "reason_category": dispute.reason_category,
            "explanation": dispute.explanation,
            "status": dispute.status,
            "admin_note": dispute.admin_note,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at,
        }
        for dispute in db.execute(disputes_query).scalars().all()
    ]

    return {
        "disputes": disputes,
        "counts_by_status": counts_by_status,
        "total": sum(counts_by_status.values()),
    }


def get_dispute_queue(
    db: Session,
    status: Optional[str] = None,
    limit: int = DEFAULT_DISPUTE_LIMIT,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the queue-specific name."""
    return get_disputes(db, status=status, limit=limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-open",
                    submitted_by="analyst-open",
                    proposed_overall_risk=0.8,
                    proposed_axes={"correctness": 0.9},
                    reason_category="model_mismatch",
                    explanation="Open dispute",
                    status="open",
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-resolved",
                    submitted_by="analyst-resolved",
                    proposed_overall_risk=0.4,
                    proposed_axes={"security": 0.3},
                    reason_category="missing_context",
                    explanation="Resolved dispute",
                    status="resolved",
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-rejected",
                    submitted_by="analyst-rejected",
                    proposed_overall_risk=0.6,
                    proposed_axes={"reliability": 0.7},
                    reason_category="insufficient_evidence",
                    explanation="Rejected dispute",
                    status="rejected",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes")
    def read_disputes(
        db: Session = Depends(get_session),
        status: Optional[str] = None,
        limit: int = DEFAULT_DISPUTE_LIMIT,
    ):
        return get_disputes(db, status=status, limit=limit)

    client = TestClient(test_app)
    response = client.get("/api/disputes")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["disputes"]) == 3
    assert {"open", "resolved", "rejected"}.issubset(
        payload["counts_by_status"]
    )

    filtered = client.get("/api/disputes", params={"status": "open"})
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert len(filtered_payload["disputes"]) == 1
    assert filtered_payload["disputes"][0]["status"] == "open"

    print("PASS")