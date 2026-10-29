from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def get_admin_note_coverage(session: Session = Depends(get_session)) -> dict[str, Any]:
    status_rows = session.execute(
        select(
            McpScoreDispute.status,
            func.count(McpScoreDispute.id).label("total"),
            func.sum(
                case(
                    (
                        and_(
                            McpScoreDispute.admin_note.is_not(None),
                            McpScoreDispute.admin_note != "",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("with_admin_note"),
        )
        .group_by(McpScoreDispute.status)
        .order_by(McpScoreDispute.status)
    ).all()

    by_status: list[dict[str, Any]] = []
    total = 0
    total_with_admin_note = 0

    for status, status_total, status_with_admin_note in status_rows:
        status_total = int(status_total or 0)
        status_with_admin_note = int(status_with_admin_note or 0)
        total += status_total
        total_with_admin_note += status_with_admin_note
        by_status.append(
            {
                "status": status,
                "total": status_total,
                "with_admin_note": status_with_admin_note,
                "without_admin_note": status_total - status_with_admin_note,
            }
        )

    coverage_pct = (
        round((total_with_admin_note / total) * 100.0, 2) if total else 0.0
    )

    oldest_unresolved_created_at = session.execute(
        select(func.min(McpScoreDispute.created_at)).where(
            McpScoreDispute.resolved_at.is_(None)
        )
    ).scalar()

    resolved_rows = session.execute(
        select(
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        ).where(McpScoreDispute.resolved_at.is_not(None))
    ).all()

    resolution_hours = [
        (resolved_at - created_at).total_seconds() / 3600.0
        for created_at, resolved_at in resolved_rows
        if created_at is not None and resolved_at is not None
    ]
    mean_resolution_hours = (
        round(sum(resolution_hours) / len(resolution_hours), 2)
        if resolution_hours
        else None
    )

    return {
        "total": total,
        "by_status": by_status,
        "coverage_pct": coverage_pct,
        "oldest_unresolved_created_at": oldest_unresolved_created_at,
        "mean_resolution_hours": mean_resolution_hours,
    }


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(that_engine)
    that_session_factory = sessionmaker(
        bind=that_engine,
        autoflush=False,
        autocommit=False,
    )

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    that_session = that_session_factory()
    that_session.add_all(
        [
            McpScoreDispute(
                server_id="server-1",
                submitted_by="admin-1",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="test",
                explanation="test",
                status="resolved",
                admin_note="Reviewed",
                created_at=base_time,
                resolved_at=base_time + timedelta(hours=2),
            ),
            McpScoreDispute(
                server_id="server-2",
                submitted_by="admin-1",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="test",
                explanation="test",
                status="resolved",
                admin_note="Approved",
                created_at=base_time + timedelta(hours=1),
                resolved_at=base_time + timedelta(hours=3),
            ),
            McpScoreDispute(
                server_id="server-3",
                submitted_by="admin-1",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="test",
                explanation="test",
                status="open",
                admin_note=None,
                created_at=base_time + timedelta(hours=2),
                resolved_at=None,
            ),
            McpScoreDispute(
                server_id="server-4",
                submitted_by="admin-1",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="test",
                explanation="test",
                status="open",
                admin_note="",
                created_at=base_time + timedelta(hours=4),
                resolved_at=None,
            ),
        ]
    )
    that_session.commit()

    def override_get_session():
        yield that_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/disputes/admin-note-coverage")
    def admin_note_coverage_endpoint(session: Session = Depends(get_session)):
        return get_admin_note_coverage(session)

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/admin-note-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["coverage_pct"] == 50.0
        assert payload["mean_resolution_hours"] == 2.0
        counts = {row["status"]: row for row in payload["by_status"]}
        assert counts["resolved"]["total"] == 2
        assert counts["resolved"]["with_admin_note"] == 2
        assert counts["resolved"]["without_admin_note"] == 0
        assert counts["open"]["total"] == 2
        assert counts["open"]["with_admin_note"] == 0
        assert counts["open"]["without_admin_note"] == 2

    print("PASS")