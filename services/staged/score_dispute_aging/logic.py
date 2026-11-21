"""logic.py -- the service's data/computation layer (one concern, one file)."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_STATUSES = ("open", "resolved", "rejected")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, accepting values already returned by the DB."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(created_at: Any, now: datetime) -> Optional[float]:
    parsed = _parse_timestamp(created_at)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def get_dispute_aging(db: Session) -> Dict[str, Any]:
    """Return dispute counts, open-dispute ages, and reason-category totals."""
    disputes = db.execute(select(McpScoreDispute)).scalars().all()
    now = datetime.now(timezone.utc)

    counts = {status: 0 for status in _STATUSES}
    open_ages: List[float] = []
    reason_counts: Dict[str, int] = {}
    reason_open_counts: Dict[str, int] = {}

    for dispute in disputes:
        status = str(dispute.status or "").strip().lower()
        if status not in counts:
            continue

        counts[status] += 1

        reason = dispute.reason_category or "unknown"
        reason = str(reason)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if status == "open":
            reason_open_counts[reason] = reason_open_counts.get(reason, 0) + 1
            age = _age_days(dispute.created_at, now)
            if age is not None:
                open_ages.append(age)

    buckets: List[Dict[str, Any]] = []
    for status in _STATUSES:
        if status == "open" and open_ages:
            median_age = float(median(open_ages))
            oldest = float(max(open_ages))
        else:
            median_age = None
            oldest = None
        buckets.append(
            {
                "status": status,
                "count": counts[status],
                "median_age_days": median_age,
                "oldest_open_days": oldest,
            }
        )

    by_reason = [
        {
            "reason_category": reason,
            "count": reason_counts[reason],
            "open_count": reason_open_counts.get(reason, 0),
        }
        for reason in sorted(reason_counts)
    ]

    return {
        "generated_at": now.isoformat(),
        "buckets": buckets,
        "by_reason": by_reason,
        "total_open": counts["open"],
    }


compute_dispute_aging = get_dispute_aging
dispute_aging_summary = get_dispute_aging


if __name__ == "__main__":
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
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed_time = datetime.now(timezone.utc)
    rows = [
        McpScoreDispute(
            id=1,
            server_id="server-a",
            submitted_by="alice",
            proposed_overall_risk="high",
            proposed_axes={"safety": 0.9},
            reason_category="policy",
            explanation="A policy disagreement",
            status="open",
            created_at=seed_time - timedelta(days=3),
        ),
        McpScoreDispute(
            id=2,
            server_id="server-b",
            submitted_by="bob",
            proposed_overall_risk="medium",
            proposed_axes={"privacy": 0.6},
            reason_category="quality",
            explanation="A quality disagreement",
            status="open",
            created_at=seed_time - timedelta(days=1),
        ),
        McpScoreDispute(
            id=3,
            server_id="server-a",
            submitted_by="alice",
            proposed_overall_risk="low",
            proposed_axes={"safety": 0.2},
            reason_category="policy",
            explanation="A resolved policy disagreement",
            status="resolved",
            created_at=seed_time - timedelta(days=5),
            resolved_at=seed_time - timedelta(days=2),
        ),
        McpScoreDispute(
            id=4,
            server_id="server-c",
            submitted_by="carol",
            proposed_overall_risk="medium",
            proposed_axes={"quality": 0.5},
            reason_category="quality",
            explanation="A resolved quality disagreement",
            status="resolved",
            created_at=seed_time - timedelta(days=4),
            resolved_at=seed_time - timedelta(days=1),
        ),
        McpScoreDispute(
            id=5,
            server_id="server-c",
            submitted_by="carol",
            proposed_overall_risk="low",
            proposed_axes={"safety": 0.1},
            reason_category="other",
            explanation="A rejected disagreement",
            status="rejected",
            created_at=seed_time - timedelta(days=2),
            resolved_at=seed_time - timedelta(days=1),
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/disputes/aging")
    def aging_endpoint(session: Session = Depends(get_session)):
        return get_dispute_aging(session)

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get("/api/disputes/aging")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_open"] == 2
    assert next(bucket["count"] for bucket in payload["buckets"] if bucket["status"] == "open") == 2
    assert next(item["count"] for item in payload["by_reason"] if item["reason_category"] == "policy") == 2
    print("PASS")