from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api")


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/disputes/submitters/leaderboard")
def get_leaderboard(
    limit: int = Query(50, ge=1),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    aggregate_sql = """
        SELECT
            submitted_by,
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status NOT IN ('resolved', 'rejected') THEN 1
                    ELSE 0
                END
            ) AS open,
            SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
        FROM mcp_score_disputes
        GROUP BY submitted_by
        ORDER BY COUNT(*) DESC, submitted_by ASC
        LIMIT :limit
    """
    aggregate_rows = db.execute(
        select(
            McpScoreDispute.submitted_by,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        ).where(McpScoreDispute.status == "resolved")
    ).all()

    duration_totals: dict[Any, list[float]] = {}
    for submitted_by, created_at, resolved_at in aggregate_rows:
        if created_at is None or resolved_at is None:
            continue
        duration = (_as_utc(resolved_at) - _as_utc(created_at)).total_seconds() / 3600
        totals = duration_totals.setdefault(submitted_by, [0.0, 0.0])
        totals[0] += duration
        totals[1] += 1

    from sqlalchemy import text

    grouped = db.execute(text(aggregate_sql), {"limit": limit}).mappings().all()

    submitters = []
    for row in grouped:
        submitter = row["submitted_by"]
        totals = duration_totals.get(submitter)
        average = round(totals[0] / totals[1], 2) if totals and totals[1] else None
        submitters.append(
            {
                "submitted_by": str(submitter),
                "total": int(row["total"]),
                "open": int(row["open"] or 0),
                "resolved": int(row["resolved"] or 0),
                "rejected": int(row["rejected"] or 0),
                "avg_resolution_hours": average,
            }
        )

    return {
        "limit": limit,
        "submitters": submitters,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id=1,
                    submitted_by="alice",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    reason_category="test",
                    explanation="Open dispute",
                    status="open",
                    admin_note=None,
                    created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="alice",
                    proposed_overall_risk=0.6,
                    proposed_axes={},
                    reason_category="test",
                    explanation="Resolved dispute",
                    status="resolved",
                    admin_note="Reviewed",
                    created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    resolved_at=datetime(2025, 1, 1, 12, tzinfo=timezone.utc),
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="bob",
                    proposed_overall_risk=0.4,
                    proposed_axes={},
                    reason_category="test",
                    explanation="Rejected dispute",
                    status="rejected",
                    admin_note="Invalid",
                    created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    resolved_at=datetime(2025, 1, 1, 6, tzinfo=timezone.utc),
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="bob",
                    proposed_overall_risk=0.3,
                    proposed_axes={},
                    reason_category="test",
                    explanation="Resolved dispute",
                    status="resolved",
                    admin_note="Reviewed",
                    created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    resolved_at=datetime(2025, 1, 3, 6, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/disputes/submitters/leaderboard", params={"limit": 10}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 10
    assert len(payload["submitters"]) == 2
    alice = next(item for item in payload["submitters"] if item["submitted_by"] == "alice")
    assert alice["total"] == 2
    assert alice["open"] + alice["resolved"] == alice["total"]
    assert alice["avg_resolution_hours"] == 12.0
    print("PASS")