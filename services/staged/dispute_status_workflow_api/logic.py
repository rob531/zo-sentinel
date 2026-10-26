from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import bindparam, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api/disputes/status-workflow", tags=["disputes"])


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _label(value: str | None) -> str:
    return value.strip() if value and value.strip() else "unknown"


@router.get("")
def get_dispute_status_workflow(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    days = max(1, min(365, days))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    statement = (
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
        .where(McpScoreDispute.created_at >= bindparam("cutoff"))
        .order_by(McpScoreDispute.created_at.desc(), McpScoreDispute.id)
    )
    rows = session.execute(statement, {"cutoff": cutoff}).all()

    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    resolution_hours: list[float] = []

    for row in rows:
        status = _label(row.status)
        reason = _label(row.reason_category)
        status_counts[status] = status_counts.get(status, 0) + 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if (row.status or "").strip().lower() != "resolved":
            continue
        if row.created_at is None or row.resolved_at is None:
            continue

        created_at = _utc_naive(row.created_at)
        resolved_at = _utc_naive(row.resolved_at)
        elapsed = (resolved_at - created_at).total_seconds() / 3600
        if elapsed >= 0:
            resolution_hours.append(elapsed)

    by_status = [
        {"status": status, "count": count}
        for status, count in sorted(
            status_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    by_reason = [
        {"reason_category": reason, "count": count}
        for reason, count in sorted(
            reason_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    open_disputes = sum(
        count
        for status, count in status_counts.items()
        if status.lower() not in {"resolved", "rejected", "closed"}
    )

    average_resolution = (
        float(sum(resolution_hours) / len(resolution_hours))
        if resolution_hours
        else None
    )
    median_resolution = float(median(resolution_hours)) if resolution_hours else None

    return {
        "days": days,
        "by_status": by_status,
        "by_reason": by_reason,
        "open_disputes": open_disputes,
        "avg_resolution_hours": average_resolution,
        "median_resolution_hours": median_resolution,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="server-test",
                    submitted_by="tester",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    reason_category="scoring",
                    explanation="Resolved test dispute",
                    status="resolved",
                    admin_note="reviewed",
                    created_at=now - timedelta(hours=5),
                    resolved_at=now - timedelta(hours=3),
                ),
                McpScoreDispute(
                    server_id="server-test",
                    submitted_by="tester",
                    proposed_overall_risk=0.6,
                    proposed_axes={},
                    reason_category="scoring",
                    explanation="Open test dispute",
                    status="open",
                    admin_note=None,
                    created_at=now - timedelta(hours=1),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="server-test",
                    submitted_by="tester",
                    proposed_overall_risk=0.7,
                    proposed_axes={},
                    reason_category="data_quality",
                    explanation="Rejected test dispute",
                    status="rejected",
                    admin_note="rejected",
                    created_at=now - timedelta(days=2),
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/status-workflow", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert sum(item["count"] for item in payload["by_status"]) == 3
        assert payload["open_disputes"] == 1
        assert payload["avg_resolution_hours"] is not None
        assert payload["avg_resolution_hours"] == 2.0

    print("PASS")