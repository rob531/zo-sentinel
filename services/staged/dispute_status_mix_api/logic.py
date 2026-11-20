"""logic.py -- the service's data/computation layer (one concern, one file).

Reads authoritative dispute data through the real app SQLAlchemy session and
keeps the FastAPI router responsible only for request and response handling.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

STATUSES = (
    "open",
    "under_review",
    "approved",
    "rejected",
    "withdrawn",
    "unknown",
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalized(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    return normalized or "unknown"


def get_dispute_status_mix(db: Session, days: int = 30) -> Dict[str, object]:
    """Return dispute status, reason, resolution, and stale-open statistics."""
    if days <= 0:
        raise ValueError("days must be greater than zero")

    disputes = db.execute(select(McpScoreDispute)).scalars().all()
    now = datetime.now(timezone.utc)
    resolution_cutoff = now - timedelta(days=days)
    stale_cutoff = now - timedelta(days=30)

    by_status = {status: 0 for status in STATUSES}
    by_reason_category: Dict[str, int] = {}
    resolution_hours = []
    stale_open_count = 0

    for dispute in disputes:
        status = _normalized(dispute.status)
        by_status[status if status in by_status else "unknown"] += 1

        category = _normalized(dispute.reason_category)
        by_reason_category[category] = by_reason_category.get(category, 0) + 1

        if dispute.created_at is not None and dispute.resolved_at is not None:
            created_at = _as_utc(dispute.created_at)
            resolved_at = _as_utc(dispute.resolved_at)
            if resolved_at >= resolution_cutoff and resolved_at >= created_at:
                resolution_hours.append(
                    (resolved_at - created_at).total_seconds() / 3600.0
                )

        if (
            status == "open"
            and dispute.created_at is not None
            and _as_utc(dispute.created_at) < stale_cutoff
        ):
            stale_open_count += 1

    median_resolution_hours = (
        float(median(resolution_hours)) if resolution_hours else None
    )

    return {
        "days": days,
        "total": len(disputes),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "median_resolution_hours": median_resolution_hours,
        "stale_open_count": stale_open_count,
    }


get_status_mix = get_dispute_status_mix
compute_status_mix = get_dispute_status_mix


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seeded = [
        McpScoreDispute(
            server_id=1,
            status="open",
            reason_category="ip_reputation",
            proposed_overall_risk="high",
            explanation="Stale open dispute",
            submitted_by="self-test",
            created_at=now - timedelta(days=45),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=1,
            status="open",
            reason_category="domain_age",
            proposed_overall_risk="medium",
            explanation="Recent open dispute",
            submitted_by="self-test",
            created_at=now - timedelta(days=2),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=1,
            status="under_review",
            reason_category="scanner_behavior",
            proposed_overall_risk="medium",
            explanation="Dispute under review",
            submitted_by="self-test",
            created_at=now - timedelta(days=3),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=1,
            status="approved",
            reason_category="ip_reputation",
            proposed_overall_risk="low",
            explanation="Approved dispute",
            submitted_by="self-test",
            created_at=now - timedelta(days=4),
            resolved_at=now - timedelta(days=3),
        ),
        McpScoreDispute(
            server_id=1,
            status="approved",
            reason_category="documentation",
            proposed_overall_risk="low",
            explanation="Approved documentation correction",
            submitted_by="self-test",
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=4, hours=12),
        ),
        McpScoreDispute(
            server_id=1,
            status="rejected",
            reason_category="scanner_behavior",
            proposed_overall_risk="high",
            explanation="Rejected dispute",
            submitted_by="self-test",
            created_at=now - timedelta(days=6),
            resolved_at=now - timedelta(days=5),
        ),
        McpScoreDispute(
            server_id=1,
            status="withdrawn",
            reason_category="domain_age",
            proposed_overall_risk="low",
            explanation="Withdrawn dispute",
            submitted_by="self-test",
            created_at=now - timedelta(days=7),
            resolved_at=now - timedelta(days=6),
        ),
        McpScoreDispute(
            server_id=1,
            status="unknown",
            reason_category="other",
            proposed_overall_risk="medium",
            explanation="Unknown dispute status",
            submitted_by="self-test",
            created_at=now - timedelta(days=8),
            resolved_at=now - timedelta(days=7),
        ),
    ]

    with TestSession() as session:
        session.add_all(seeded)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/disputes/status-mix")
    def status_mix_endpoint(days: int = 90, db: Session = Depends(get_session)):
        return get_dispute_status_mix(db, days)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/disputes/status-mix?days=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 8
    assert sum(payload["by_status"].values()) == 8
    assert payload["stale_open_count"] == 1
    print("PASS")