from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc_datetime(value).isoformat()


def get_dispute_status_aging(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    disputes = list(session.execute(select(McpScoreDispute)).scalars().all())
    now = datetime.now(timezone.utc)

    status_rows: dict[str, list[McpScoreDispute]] = defaultdict(list)
    reason_counts: Counter[str | None] = Counter()

    for dispute in disputes:
        status_rows[dispute.status or "UNKNOWN"].append(dispute)
        reason_counts[dispute.reason_category] += 1

    by_status: list[dict[str, Any]] = []
    for status in sorted(status_rows):
        rows = status_rows[status]
        created_times = [_utc_datetime(row.created_at) for row in rows]
        ages = [
            max(0.0, (now - created_at).total_seconds() / 3600.0)
            for created_at in created_times
        ]
        resolved_count = sum(
            1 for row in rows if (row.status or "").upper() == "RESOLVED"
        )

        by_status.append(
            {
                "status": status,
                "count": len(rows),
                "oldest_created_at": _timestamp(min(created_times)) if created_times else None,
                "newest_created_at": _timestamp(max(created_times)) if created_times else None,
                "avg_age_hours": round(sum(ages) / len(ages), 2) if ages else 0.0,
                "resolved_count": resolved_count,
            }
        )

    reason_categories = [
        {"reason_category": reason_category, "count": count}
        for reason_category, count in sorted(
            reason_counts.items(),
            key=lambda item: (-item[1], item[0] or ""),
        )
    ]

    return {
        "total": len(disputes),
        "by_status": by_status,
        "reason_categories": reason_categories,
    }


get_status_aging = get_dispute_status_aging
get_dispute_aging = get_dispute_status_aging
get_status_aging_summary = get_dispute_status_aging
get_dispute_status_aging_summary = get_dispute_status_aging
compute_status_aging = get_dispute_status_aging


def _self_test() -> None:
    from fastapi import FastAPI
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
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    with test_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="test-server",
                    submitted_by="tester",
                    proposed_overall_risk=80.0,
                    proposed_axes="{}",
                    reason_category="FALSE_POSITIVE",
                    explanation="First dispute",
                    status="OPEN",
                    admin_note=None,
                    created_at=datetime(2024, 1, 1, 0, 0, 0),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=2,
                    server_id="test-server",
                    submitted_by="tester",
                    proposed_overall_risk=20.0,
                    proposed_axes="{}",
                    reason_category="FALSE_NEGATIVE",
                    explanation="Second dispute",
                    status="OPEN",
                    admin_note=None,
                    created_at=datetime(2024, 1, 2, 0, 0, 0),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=3,
                    server_id="test-server",
                    submitted_by="tester",
                    proposed_overall_risk=50.0,
                    proposed_axes="{}",
                    reason_category="OTHER",
                    explanation="Third dispute",
                    status="RESOLVED",
                    admin_note="Reviewed",
                    created_at=datetime(2024, 1, 3, 0, 0, 0),
                    resolved_at=datetime(2024, 1, 4, 0, 0, 0),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/status-aging")
    def status_aging(session: Session = Depends(get_session)):
        return get_dispute_status_aging(session)

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/status-aging")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["by_status"]) == 2
    status_counts = {row["status"]: row["count"] for row in payload["by_status"]}
    assert status_counts["OPEN"] == 2
    assert status_counts["RESOLVED"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()