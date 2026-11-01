"""Data and computation layer for dispute disposition statistics."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _resolution_hours(created_at: datetime, resolved_at: datetime) -> float:
    """Return a timezone-safe elapsed duration in hours."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
    else:
        resolved_at = resolved_at.astimezone(timezone.utc)

    return (resolved_at - created_at).total_seconds() / 3600.0


def get_dispute_disposition_stats(db: Session) -> Dict[str, Any]:
    """Compute dispute counts and median resolution time from app disputes."""
    rows = db.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
    ).all()

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()
    resolution_hours: List[float] = []

    for status, reason_category, proposed_overall_risk, created_at, resolved_at in rows:
        status_counts[str(status) if status is not None else "unknown"] += 1
        reason_counts[
            str(reason_category) if reason_category is not None else "unknown"
        ] += 1
        tier_counts[
            str(proposed_overall_risk)
            if proposed_overall_risk is not None
            else "unknown"
        ] += 1

        if created_at is not None and resolved_at is not None:
            hours = _resolution_hours(created_at, resolved_at)
            if hours >= 0:
                resolution_hours.append(hours)

    return {
        "total": len(rows),
        "by_status": dict(sorted(status_counts.items())),
        "by_reason_category": dict(sorted(reason_counts.items())),
        "proposed_tier_mix": dict(sorted(tier_counts.items())),
        "median_resolution_hours": (
            float(median(resolution_hours)) if resolution_hours else 0.0
        ),
    }


def get_disposition_stats(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the dispute disposition statistics operation."""
    return get_dispute_disposition_stats(db)


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    created_at = datetime(2025, 1, 1, 12, 0, 0)
    seed_disputes = [
        McpScoreDispute(
            id=1,
            server_id=1,
            submitted_by=1,
            proposed_overall_risk="HIGH",
            proposed_axes=None,
            reason_category="false_positive",
            status="OPEN",
            admin_note=None,
            explanation="Open dispute",
            created_at=created_at,
            resolved_at=None,
        ),
        McpScoreDispute(
            id=2,
            server_id=2,
            submitted_by=1,
            proposed_overall_risk="MEDIUM",
            proposed_axes=None,
            reason_category="context_mismatch",
            status="RESOLVED",
            admin_note="Resolved",
            explanation="Resolved dispute",
            created_at=created_at,
            resolved_at=created_at + timedelta(hours=24),
        ),
        McpScoreDispute(
            id=3,
            server_id=3,
            submitted_by=2,
            proposed_overall_risk="LOW",
            proposed_axes=None,
            reason_category="false_positive",
            status="REJECTED",
            admin_note="Rejected",
            explanation="Rejected dispute",
            created_at=created_at,
            resolved_at=None,
        ),
        McpScoreDispute(
            id=4,
            server_id=4,
            submitted_by=2,
            proposed_overall_risk="HIGH",
            proposed_axes=None,
            reason_category="context_mismatch",
            status="OPEN",
            admin_note=None,
            explanation="Second open dispute",
            created_at=created_at,
            resolved_at=None,
        ),
    ]

    with testing_session() as db:
        db.add_all(seed_disputes)
        db.commit()

    self_test_app = FastAPI()

    def override_get_session():
        with testing_session() as db:
            yield db

    self_test_app.dependency_overrides[get_session] = override_get_session

    @self_test_app.get("/api/disputes/disposition-stats")
    def dispute_disposition_stats(db: Session = Depends(get_session)):
        return get_dispute_disposition_stats(db)

    response = TestClient(self_test_app).get(
        "/api/disputes/disposition-stats"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["by_status"] == {"OPEN": 2, "REJECTED": 1, "RESOLVED": 1}
    assert payload["by_reason_category"] == {
        "context_mismatch": 2,
        "false_positive": 2,
    }
    assert payload["proposed_tier_mix"] == {"HIGH": 2, "LOW": 1, "MEDIUM": 1}
    assert payload["median_resolution_hours"] == 24.0
    print("PASS")


if __name__ == "__main__":
    _self_test()