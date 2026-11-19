"""Data and computation layer for the disputes ageing queue."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute, McpServerRegistry


_BUCKETS = ("0-1d", "1-3d", "3-7d", "7-30d", "30d+")


def _age_days(created_at: datetime) -> float:
    """Return a non-negative age in days for a dispute timestamp."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    age = (datetime.now(timezone.utc) - created_at).total_seconds()
    return max(0.0, age / 86400.0)


def _bucket_for_age(age_days: float) -> str:
    if age_days < 1:
        return "0-1d"
    if age_days < 3:
        return "1-3d"
    if age_days < 7:
        return "3-7d"
    if age_days < 30:
        return "7-30d"
    return "30d+"


def get_ageing_queue(db: Session) -> Dict[str, Any]:
    """Build the open-dispute ageing queue from the application database."""
    rows = db.execute(
        select(McpScoreDispute, McpServerRegistry.name)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(
            and_(
                McpScoreDispute.status != "resolved",
                McpScoreDispute.resolved_at.is_(None),
            )
        )
        .order_by(McpScoreDispute.created_at.asc())
    ).all()

    counts = {bucket: 0 for bucket in _BUCKETS}
    oldest: List[Dict[str, Any]] = []

    for dispute, server_name in rows:
        age_days = _age_days(dispute.created_at)
        counts[_bucket_for_age(age_days)] += 1

        oldest.append(
            {
                "id": int(dispute.id),
                "server_id": dispute.server_id,
                "server_name": server_name,
                "reason_category": dispute.reason_category,
                "age_days": age_days,
                "proposed_overall_risk": dispute.proposed_overall_risk,
            }
        )

    return {
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in _BUCKETS
        ],
        "oldest": oldest[:25],
        "total_open": len(rows),
    }


# Keep the common service naming variants available to thin routers.
get_disputes_ageing_queue = get_ageing_queue
build_ageing_queue = get_ageing_queue
disputes_ageing_queue = get_ageing_queue


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    db.add_all(
        [
            McpServerRegistry(server_id="server-a", name="Server A"),
            McpServerRegistry(server_id="server-b", name="Server B"),
            McpScoreDispute(
                server_id="server-a",
                submitted_by="admin-a",
                proposed_overall_risk="high",
                proposed_axes={},
                reason_category="appeal",
                explanation="Open recent dispute",
                status="pending",
                created_at=now - timedelta(days=2),
            ),
            McpScoreDispute(
                server_id="server-b",
                submitted_by="admin-b",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="appeal",
                explanation="Open old dispute",
                status="pending",
                created_at=now - timedelta(days=10),
            ),
            McpScoreDispute(
                server_id="server-a",
                submitted_by="admin-a",
                proposed_overall_risk="medium",
                proposed_axes={},
                reason_category="appeal",
                explanation="Resolved dispute",
                status="resolved",
                created_at=now - timedelta(days=12),
                resolved_at=now,
            ),
            McpScoreDispute(
                server_id="missing-server",
                submitted_by="admin-a",
                proposed_overall_risk="high",
                proposed_axes={},
                reason_category="appeal",
                explanation="Open dispute for an unknown server",
                status="pending",
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    db.commit()

    def override_get_session():
        yield db

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/ageing")
    def ageing_endpoint(session: Session = Depends(get_session)):
        return get_ageing_queue(session)

    response = TestClient(test_app).get("/api/disputes/ageing")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_open"] == 3
    assert any(
        item["server_id"] == "missing-server" and item["server_name"] is None
        for item in payload["oldest"]
    )
    assert any(
        item["server_id"] == "server-b"
        and next(
            bucket["count"]
            for bucket in payload["buckets"]
            if bucket["bucket"] == "7-30d"
        )
        > 0
        for item in payload["oldest"]
    )
    assert all(item["id"] != 3 for item in payload["oldest"])

    db.close()
    print("PASS")