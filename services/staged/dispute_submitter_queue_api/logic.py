from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_RESOLVED_STATUSES = {"resolved", "closed", "approved", "rejected"}


def _as_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _is_resolved(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in _RESOLVED_STATUSES


def get_dispute_submitters(
    db: Session = Depends(get_session),
    status: Optional[str] = "",
    days: int = 30,
) -> dict[str, Any]:
    statement = select(McpScoreDispute)

    normalized_status = (status or "").strip()
    if normalized_status:
        statement = statement.where(McpScoreDispute.status == normalized_status)

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        statement = statement.where(McpScoreDispute.created_at >= cutoff)

    disputes = list(db.execute(statement).scalars().all())
    grouped: dict[str, list[McpScoreDispute]] = defaultdict(list)

    for dispute in disputes:
        grouped[dispute.submitted_by].append(dispute)

    submitters: list[dict[str, Any]] = []
    for submitted_by in sorted(grouped):
        rows = grouped[submitted_by]
        resolved_rows = [
            row for row in rows if _is_resolved(row.status) and row.resolved_at is not None
        ]

        resolution_hours: list[float] = []
        for row in resolved_rows:
            if row.created_at is None or row.resolved_at is None:
                continue
            created_at = row.created_at
            resolved_at = row.resolved_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if resolved_at.tzinfo is None:
                resolved_at = resolved_at.replace(tzinfo=timezone.utc)
            resolution_hours.append(
                max(0.0, (resolved_at - created_at).total_seconds() / 3600.0)
            )

        last_dispute_at = max(
            (row.created_at for row in rows if row.created_at is not None),
            default=None,
        )

        submitters.append(
            {
                "submitted_by": submitted_by,
                "open_disputes": sum(1 for row in rows if not _is_resolved(row.status)),
                "total_disputes": len(rows),
                "avg_resolution_hours": (
                    sum(resolution_hours) / len(resolution_hours)
                    if resolution_hours
                    else 0.0
                ),
                "last_dispute_at": _as_iso(last_dispute_at),
            }
        )

    return {
        "status": normalized_status,
        "days": days,
        "submitters": submitters,
        "total_disputes": len(disputes),
    }


def get_submitters(
    db: Session = Depends(get_session),
    status: Optional[str] = "",
    days: int = 30,
) -> dict[str, Any]:
    return get_dispute_submitters(db=db, status=status, days=days)


def list_submitters(
    db: Session = Depends(get_session),
    status: Optional[str] = "",
    days: int = 30,
) -> dict[str, Any]:
    return get_dispute_submitters(db=db, status=status, days=days)


def list_dispute_submitters(
    db: Session = Depends(get_session),
    status: Optional[str] = "",
    days: int = 30,
) -> dict[str, Any]:
    return get_dispute_submitters(db=db, status=status, days=days)


def get_submitter_stats(
    db: Session = Depends(get_session),
    status: Optional[str] = "",
    days: int = 30,
) -> dict[str, Any]:
    return get_dispute_submitters(db=db, status=status, days=days)


if __name__ == "__main__":
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    rows = [
        McpScoreDispute(
            server_id="server-1",
            submitted_by="alice",
            proposed_overall_risk=80,
            proposed_axes={},
            explanation="first",
            reason_category="risk",
            status="open",
            created_at=now - timedelta(hours=3),
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="alice",
            proposed_overall_risk=70,
            proposed_axes={},
            explanation="second",
            reason_category="risk",
            status="resolved",
            created_at=now - timedelta(hours=8),
            resolved_at=now - timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id="server-3",
            submitted_by="bob",
            proposed_overall_risk=60,
            proposed_axes={},
            explanation="third",
            reason_category="risk",
            status="open",
            created_at=now - timedelta(days=1),
        ),
        McpScoreDispute(
            server_id="server-4",
            submitted_by="bob",
            proposed_overall_risk=50,
            proposed_axes={},
            explanation="fourth",
            reason_category="risk",
            status="resolved",
            created_at=now - timedelta(days=2),
            resolved_at=now - timedelta(days=1),
        ),
    ]
    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/submitters")
    def dispute_submitters(
        status: str = "",
        days: int = 30,
        db: Session = Depends(get_session),
    ):
        return get_dispute_submitters(db=db, status=status, days=days)

    response = TestClient(test_app).get(
        "/api/disputes/submitters",
        params={"status": "", "days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["submitters"]) == 2
    assert sum(item["open_disputes"] for item in payload["submitters"]) == 2
    assert payload["total_disputes"] == 4
    print("PASS")