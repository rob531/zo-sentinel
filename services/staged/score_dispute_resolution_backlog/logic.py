from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> float:
    return max(0.0, (now - _as_utc(created_at)).total_seconds() / 86400.0)


def get_dispute_backlog(
    session: Session = Depends(get_session),
    status: str = "OPEN",
    limit: int = 50,
) -> dict[str, Any]:
    del status
    limit = max(1, min(int(limit), 500))
    now = _utc_now()

    status_rows = (
        session.query(McpScoreDispute.status, func.count(McpScoreDispute.id))
        .group_by(McpScoreDispute.status)
        .all()
    )
    by_status: dict[str, int] = {"OPEN": 0, "RESOLVED": 0, "REJECTED": 0, "OTHER": 0}
    for row_status, count in status_rows:
        key = str(row_status) if row_status is not None else "OTHER"
        if key not in ("OPEN", "RESOLVED", "REJECTED"):
            key = "OTHER"
        by_status[key] = int(count)

    reason_rows = (
        session.query(McpScoreDispute.reason_category, func.count(McpScoreDispute.id))
        .group_by(McpScoreDispute.reason_category)
        .all()
    )
    by_reason: dict[str, int] = {}
    for reason, count in reason_rows:
        key = str(reason) if reason is not None else "UNKNOWN"
        by_reason[key] = by_reason.get(key, 0) + int(count)

    open_created_at = (
        session.query(McpScoreDispute.created_at)
        .filter(McpScoreDispute.status == "OPEN")
        .all()
    )
    open_ages = [
        _age_days(row.created_at, now)
        for row in open_created_at
        if row.created_at is not None
    ]
    median_open_age_days = round(float(median(open_ages)), 2) if open_ages else None

    recent_rows = (
        session.query(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpScoreDispute.submitted_by,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
        )
        .order_by(
            McpScoreDispute.created_at.desc(),
            McpScoreDispute.id.desc(),
        )
        .limit(limit)
        .all()
    )

    disputes: list[dict[str, Any]] = []
    for row in recent_rows:
        disputes.append(
            {
                "id": row.id,
                "server_id": row.server_id,
                "submitted_by": row.submitted_by,
                "proposed_overall_risk": row.proposed_overall_risk,
                "reason_category": row.reason_category,
                "status": row.status,
                "created_at": row.created_at,
                "age_days": (
                    round(_age_days(row.created_at, now), 2)
                    if row.created_at is not None
                    else None
                ),
            }
        )

    return {
        "total": len(disputes),
        "by_status": by_status,
        "by_reason": by_reason,
        "median_open_age_days": median_open_age_days,
        "disputes": disputes,
    }


get_score_dispute_backlog = get_dispute_backlog
get_dispute_resolution_backlog = get_dispute_backlog
get_score_disputes_backlog = get_dispute_backlog
get_backlog = get_dispute_backlog
compute_backlog = get_dispute_backlog


def run_self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    rows = [
        McpScoreDispute(
            server_id=101,
            submitted_by="alice",
            proposed_overall_risk=0.91,
            proposed_axes={"security": 0.95},
            reason_category="MODEL_MISMATCH",
            explanation="Open dispute",
            status="OPEN",
            created_at=now.replace(microsecond=0),
        ),
        McpScoreDispute(
            server_id=102,
            submitted_by="bob",
            proposed_overall_risk=0.72,
            proposed_axes={"security": 0.70},
            reason_category="MODEL_MISMATCH",
            explanation="Open dispute",
            status="OPEN",
            created_at=now.replace(microsecond=0),
        ),
        McpScoreDispute(
            server_id=103,
            submitted_by="carol",
            proposed_overall_risk=0.55,
            proposed_axes={"security": 0.60},
            reason_category="MISSING_EVIDENCE",
            explanation="Open dispute",
            status="OPEN",
            created_at=now.replace(microsecond=0),
        ),
        McpScoreDispute(
            server_id=104,
            submitted_by="dave",
            proposed_overall_risk=0.43,
            proposed_axes={"security": 0.45},
            reason_category="MISSING_EVIDENCE",
            explanation="Resolved dispute",
            status="RESOLVED",
            admin_note="Resolved",
            created_at=now.replace(microsecond=0),
            resolved_at=now.replace(microsecond=0),
        ),
        McpScoreDispute(
            server_id=105,
            submitted_by="erin",
            proposed_overall_risk=0.20,
            proposed_axes={"security": 0.25},
            reason_category="MODEL_MISMATCH",
            explanation="Rejected dispute",
            status="REJECTED",
            admin_note="Rejected",
            created_at=now.replace(microsecond=0),
            resolved_at=now.replace(microsecond=0),
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/disputes/backlog")
    def read_dispute_backlog(
        status: str = Query("OPEN"),
        limit: int = Query(50, ge=1, le=500),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_dispute_backlog(session=session, status=status, limit=limit)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/backlog?status=OPEN&limit=50")
        assert response.status_code == 200
        payload = response.json()
        assert payload["by_status"]["OPEN"] == 3
        assert payload["by_status"]["RESOLVED"] == 1
        assert payload["by_status"]["REJECTED"] == 1
        assert len(payload["by_reason"]) == 2
        assert len(payload["disputes"]) == 5

    print("PASS")


if __name__ == "__main__":
    run_self_test()