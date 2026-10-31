from collections import Counter
from datetime import datetime, timedelta, timezone
import math
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _resolution_hours(created_at: datetime, resolved_at: datetime) -> float:
    return (
        _utc_naive(resolved_at) - _utc_naive(created_at)
    ).total_seconds() / 3600.0


def compute_resolution_latency(
    session: Session, days: int = 30
) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=max(0, int(days)))
    disputes = list(
        session.execute(
            select(McpScoreDispute).where(
                McpScoreDispute.created_at >= cutoff
            )
        ).scalars()
    )

    status_counts: Counter[str] = Counter(
        {
            "open": 0,
            "in_review": 0,
            "resolved": 0,
            "rejected": 0,
        }
    )
    reason_counts: Counter[str] = Counter()
    latencies: list[float] = []

    for dispute in disputes:
        status = str(dispute.status)
        status_counts[status] += 1
        reason_counts[str(dispute.reason_category)] += 1

        if status == "resolved" and dispute.resolved_at is not None:
            latencies.append(
                _resolution_hours(dispute.created_at, dispute.resolved_at)
            )

    latencies.sort()
    median_hours = float(median(latencies)) if latencies else None
    if latencies:
        percentile_index = max(0, math.ceil(0.90 * len(latencies)) - 1)
        p90_hours = float(latencies[percentile_index])
    else:
        p90_hours = None

    resolved_count = len(latencies)
    return {
        "days": max(0, int(days)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "by_status": dict(status_counts),
        "by_reason_category": dict(reason_counts),
        "resolved_count": resolved_count,
        "unresolved_count": len(disputes) - resolved_count,
        "median_resolution_hours": median_hours,
        "p90_resolution_hours": p90_hours,
    }


get_resolution_latency = compute_resolution_latency
get_dispute_resolution_latency = compute_resolution_latency
calculate_resolution_latency = compute_resolution_latency
resolution_latency_stats = compute_resolution_latency


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

    now = datetime.utcnow()
    seed_rows = [
        McpScoreDispute(
            id=1,
            server_id=1,
            submitted_by="tester",
            proposed_overall_risk=0.8,
            explanation="First open dispute",
            reason_category="model_mismatch",
            status="open",
            created_at=now - timedelta(hours=2),
        ),
        McpScoreDispute(
            id=2,
            server_id=2,
            submitted_by="tester",
            proposed_overall_risk=0.6,
            explanation="Second open dispute",
            reason_category="data_quality",
            status="open",
            created_at=now - timedelta(hours=3),
        ),
        McpScoreDispute(
            id=3,
            server_id=3,
            submitted_by="tester",
            proposed_overall_risk=0.7,
            explanation="Dispute under review",
            reason_category="model_mismatch",
            status="in_review",
            created_at=now - timedelta(hours=4),
        ),
        McpScoreDispute(
            id=4,
            server_id=4,
            submitted_by="tester",
            proposed_overall_risk=0.9,
            explanation="One-hour resolution",
            reason_category="policy_interpretation",
            status="resolved",
            created_at=now - timedelta(hours=2),
            resolved_at=now - timedelta(hours=1),
        ),
        McpScoreDispute(
            id=5,
            server_id=5,
            submitted_by="tester",
            proposed_overall_risk=0.5,
            explanation="Forty-nine-hour resolution",
            reason_category="policy_interpretation",
            status="resolved",
            created_at=now - timedelta(hours=50),
            resolved_at=now - timedelta(hours=1),
        ),
    ]

    with TestSession.begin() as db:
        db.add_all(seed_rows)

    test_app = FastAPI()

    @test_app.get("/api/disputes/resolution-latency")
    def resolution_latency_route(
        days: int = 30, db: Session = Depends(get_session)
    ) -> dict[str, Any]:
        return compute_resolution_latency(db, days)

    test_app.dependency_overrides[get_session] = lambda: TestSession()

    response = TestClient(test_app).get(
        "/api/disputes/resolution-latency", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["resolved_count"] == 2
    assert payload["median_resolution_hours"] == 25.0
    assert payload["unresolved_count"] == 3
    print("PASS")