"""Logic for the dispute queue aging service."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

_STATUS_ORDER = ("open", "under_review", "approved", "rejected", "other")
_UNRESOLVED_STATUSES = {"open", "under_review"}


def _age_days(created_at: datetime, now: datetime) -> float:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return float(max(0, (now - created_at).days))


def dispute_queue_aging_summary(db: Session) -> Dict[str, object]:
    """Return dispute queue counts and unresolved-dispute age statistics."""
    rows = db.query(McpScoreDispute).all()
    now = datetime.now(timezone.utc)

    status_counts = {status: 0 for status in _STATUS_ORDER}
    reason_counts: Dict[str, int] = {}
    unresolved_created_at: List[datetime] = []

    for dispute in rows:
        raw_status = (dispute.status or "").strip().lower()
        status = raw_status if raw_status in status_counts else "other"
        status_counts[status] += 1

        reason = (dispute.reason_category or "").strip() or "unknown"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if raw_status in _UNRESOLVED_STATUSES and dispute.created_at is not None:
            unresolved_created_at.append(dispute.created_at)

    ages = [_age_days(created_at, now) for created_at in unresolved_created_at]
    oldest_age = max(ages) if ages else 0.0
    median_age = float(median(ages)) if ages else 0.0

    return {
        "total": int(len(rows)),
        "open": int(sum(status_counts[status] for status in _UNRESOLVED_STATUSES)),
        "unresolved_oldest_age_days": float(oldest_age),
        "median_unresolved_age_days": median_age,
        "by_status": [
            {"status": status, "count": int(status_counts[status])}
            for status in _STATUS_ORDER
        ],
        "by_reason": [
            {"reason_category": reason, "count": int(reason_counts[reason])}
            for reason in sorted(reason_counts)
        ],
    }


# Compatibility names for thin routers and direct service consumers.
get_dispute_queue_aging = dispute_queue_aging_summary
summarize_dispute_queue = dispute_queue_aging_summary
queue_aging_summary = dispute_queue_aging_summary


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import Depends, FastAPI
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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = TestingSession()
    session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id=1,
                submitted_by="analyst-1",
                proposed_overall_risk=0.8,
                reason_category="false_positive",
                explanation="Unresolved dispute one",
                status="open",
                created_at=now - timedelta(days=1),
            ),
            McpScoreDispute(
                id=2,
                server_id=2,
                submitted_by="analyst-2",
                proposed_overall_risk=0.6,
                reason_category="model_error",
                explanation="Unresolved dispute two",
                status="under_review",
                created_at=now - timedelta(days=10),
            ),
            McpScoreDispute(
                id=3,
                server_id=3,
                submitted_by="analyst-3",
                proposed_overall_risk=0.7,
                reason_category="false_positive",
                explanation="Unresolved dispute three",
                status="open",
                created_at=now - timedelta(days=60),
            ),
            McpScoreDispute(
                id=4,
                server_id=4,
                submitted_by="analyst-4",
                proposed_overall_risk=0.4,
                reason_category="context_missing",
                explanation="Resolved approved dispute",
                status="approved",
                created_at=now - timedelta(days=5),
                resolved_at=now - timedelta(days=1),
            ),
            McpScoreDispute(
                id=5,
                server_id=5,
                submitted_by="analyst-5",
                proposed_overall_risk=0.9,
                reason_category="model_error",
                explanation="Resolved rejected dispute",
                status="rejected",
                created_at=now - timedelta(days=15),
                resolved_at=now - timedelta(days=2),
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    @test_app.get("/api/disputes/queue-aging")
    def queue_aging_endpoint(db: Session = Depends(get_session)):
        return dispute_queue_aging_summary(db)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/queue-aging")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["open"] == 3
    assert payload["unresolved_oldest_age_days"] >= 59
    assert payload["median_unresolved_age_days"] == 10
    print("PASS")


if __name__ == "__main__":
    _self_test()