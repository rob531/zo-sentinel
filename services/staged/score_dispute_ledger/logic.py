"""logic.py -- the service's data/computation layer for score disputes.

Reads the authoritative application dispute queue through the real SQLAlchemy
session and model. The service is read-only and does not alter or reinterpret
proposed risk tiers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime for age calculations."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime) -> int:
    """Calculate the non-negative age of a dispute in whole days."""
    age = (datetime.now(timezone.utc) - _as_utc(created_at)).days
    return max(0, int(age))


def get_disputes(
    db: Session,
    status: Optional[str] = "open",
    limit: int = 50,
) -> Dict:
    """Return the filtered dispute queue and counts grouped by status.

    ``total`` is the number of disputes matching the requested status, while
    ``counts_by_status`` describes the complete dispute table so unresolved
    queues can be triaged alongside the filtered result.
    """
    normalized_limit = max(0, min(int(limit), 200))

    dispute_query = select(McpScoreDispute)
    count_query = select(func.count(McpScoreDispute.id))

    if status is not None:
        dispute_query = dispute_query.where(McpScoreDispute.status == status)
        count_query = count_query.where(McpScoreDispute.status == status)

    total = int(db.execute(count_query).scalar() or 0)

    if normalized_limit:
        dispute_query = dispute_query.order_by(
            McpScoreDispute.created_at.desc(),
            McpScoreDispute.id.desc(),
        ).limit(normalized_limit)
    else:
        dispute_query = dispute_query.order_by(
            McpScoreDispute.created_at.desc(),
            McpScoreDispute.id.desc(),
        )

    disputes = []
    for dispute in db.execute(dispute_query).scalars():
        disputes.append(
            {
                "id": dispute.id,
                "server_id": dispute.server_id,
                "submitted_by": dispute.submitted_by,
                "status": dispute.status,
                "reason_category": dispute.reason_category,
                "proposed_overall_risk": dispute.proposed_overall_risk,
                "created_at": dispute.created_at,
                "resolved_at": dispute.resolved_at,
                "age_days": _age_days(dispute.created_at),
            }
        )

    count_rows = db.execute(
        select(McpScoreDispute.status, func.count(McpScoreDispute.id))
        .group_by(McpScoreDispute.status)
    ).all()
    counts_by_status: Dict[str, int] = {}
    for row_status, count in count_rows:
        key = row_status if row_status is not None else "unknown"
        counts_by_status[str(key)] = int(count)

    return {
        "disputes": disputes,
        "total": total,
        "counts_by_status": counts_by_status,
    }


# Keep the common service naming variants available to thin routers.
list_disputes = get_disputes
fetch_disputes = get_disputes
get_dispute_queue = get_disputes
get_score_disputes = get_disputes


def _self_test() -> None:
    """Exercise the endpoint against a temporary in-memory SQLite fixture."""
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id=101,
                    submitted_by="analyst@example.test",
                    proposed_overall_risk="high",
                    proposed_axes={"malware": 1},
                    reason_category="tier_shift",
                    explanation="The registry understates this server's risk.",
                    status="open",
                    created_at=now,
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=2,
                    server_id=102,
                    submitted_by="analyst@example.test",
                    proposed_overall_risk="medium",
                    proposed_axes={"malware": 0},
                    reason_category="tier_shift",
                    explanation="The proposed tier should be reviewed.",
                    status="open",
                    created_at=now,
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=3,
                    server_id=103,
                    submitted_by="admin@example.test",
                    proposed_overall_risk="low",
                    proposed_axes={"malware": 0},
                    reason_category="tier_shift",
                    explanation="The proposed tier has been reviewed.",
                    status="resolved",
                    created_at=now,
                    resolved_at=now,
                ),
            ]
        )
        session.commit()

    def override_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/disputes")
    def disputes_endpoint(
        status: str = Query("open"),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_session),
    ):
        return get_disputes(db, status=status, limit=limit)

    test_app.dependency_overrides[get_session] = override_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/disputes", params={"status": "open"})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["disputes"]) == 2, payload
            assert payload["counts_by_status"].get("resolved") == 1, payload
            assert any(
                dispute["proposed_overall_risk"] == "high"
                for dispute in payload["disputes"]
            ), payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()