"""logic.py -- the service's data/computation layer (one concern, one file)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


def get_dispute_timeline(
    db: Session = Depends(get_session), days: int = 30
) -> Dict[str, object]:
    """Return dispute counts by calendar day and status for the recent window."""
    effective_days = max(1, int(days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=effective_days)

    rows = db.execute(
        select(
            func.date(McpScoreDispute.created_at).label("day"),
            McpScoreDispute.status,
            McpServerRegistry.name.label("server_name"),
            func.count().label("count"),
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.created_at >= cutoff)
        .group_by(
            func.date(McpScoreDispute.created_at),
            McpScoreDispute.status,
            McpServerRegistry.name,
        )
        .order_by(func.date(McpScoreDispute.created_at))
    ).all()

    by_status: Dict[str, int] = {}
    by_day: Dict[str, int] = {}

    for day, status, _server_name, count in rows:
        day_value = day.isoformat() if hasattr(day, "isoformat") else str(day)
        status_value = status or "unknown"
        count_value = int(count)

        by_status[status_value] = by_status.get(status_value, 0) + count_value
        by_day[day_value] = by_day.get(day_value, 0) + count_value

    series = [
        {"date": day, "count": by_day[day]}
        for day in sorted(by_day)
    ]

    return {
        "days": effective_days,
        "total": sum(by_status.values()),
        "by_status": by_status,
        "series": series,
    }


def dispute_timeline(
    db: Session = Depends(get_session), days: int = 30
) -> Dict[str, object]:
    """Compatibility wrapper for the dispute timeline computation."""
    return get_dispute_timeline(db=db, days=days)


if __name__ == "__main__":
    from fastapi import FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)

    with SessionLocal() as session:
        session.add(
            McpServerRegistry(
                server_id="self-test-server",
                name="Self Test Server",
                url="https://self-test.invalid",
            )
        )
        session.add_all(
            [
                McpScoreDispute(
                    server_id="self-test-server",
                    proposed_overall_risk=0.8,
                    reason_category="risk",
                    status="open",
                    created_at=yesterday,
                    explanation="self-test dispute",
                    submitted_by="self-test",
                ),
                McpScoreDispute(
                    server_id="self-test-server",
                    proposed_overall_risk=0.6,
                    reason_category="risk",
                    status="open",
                    created_at=yesterday,
                    explanation="self-test dispute",
                    submitted_by="self-test",
                ),
                McpScoreDispute(
                    server_id="self-test-server",
                    proposed_overall_risk=0.4,
                    reason_category="policy",
                    status="resolved",
                    created_at=now,
                    explanation="self-test dispute",
                    submitted_by="self-test",
                ),
                McpScoreDispute(
                    server_id="self-test-server",
                    proposed_overall_risk=0.2,
                    reason_category="policy",
                    status="resolved",
                    created_at=now,
                    explanation="self-test dispute",
                    submitted_by="self-test",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/disputes/timeline")
    def timeline(
        db: Session = Depends(get_session), days: int = Query(30, ge=1)
    ):
        return get_dispute_timeline(db=db, days=days)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/disputes/timeline", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert set(payload["by_status"]) == {"open", "resolved"}
    assert payload["by_status"]["open"] == 2
    assert payload["by_status"]["resolved"] == 2
    assert any(
        item["date"] == yesterday.date().isoformat() and item["count"] == 2
        for item in payload["series"]
    )
    assert any(
        item["date"] == now.date().isoformat() and item["count"] == 2
        for item in payload["series"]
    )
    print("PASS")