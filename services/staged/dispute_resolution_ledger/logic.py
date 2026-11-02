"""logic.py -- dispute resolution ledger data and computation layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime for latency arithmetic."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolution_hours(created_at: datetime, resolved_at: datetime) -> float:
    return (_as_utc(resolved_at) - _as_utc(created_at)).total_seconds() / 3600.0


def get_dispute_resolution_ledger(
    db: Session,
    status_filter: str = "all",
    days: int = 30,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the dispute resolution ledger for the requested status and window."""
    selected_status = (status if status is not None else status_filter).lower()
    if selected_status not in {"open", "resolved", "all"}:
        raise ValueError("status must be one of: open, resolved, all")
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conditions = [McpScoreDispute.created_at >= cutoff]
    if selected_status != "all":
        conditions.append(McpScoreDispute.status == selected_status)

    status_rows = db.execute(
        select(McpScoreDispute.status, func.count(McpScoreDispute.id))
        .where(*conditions)
        .group_by(McpScoreDispute.status)
    ).all()

    by_status = {str(row_status): int(count) for row_status, count in status_rows}
    total = sum(by_status.values())

    reason_rows = db.execute(
        select(McpScoreDispute.reason_category, func.count(McpScoreDispute.id))
        .where(*conditions)
        .group_by(McpScoreDispute.reason_category)
        .order_by(func.count(McpScoreDispute.id).desc(), McpScoreDispute.reason_category)
    ).all()
    by_reason = [
        {"reason_category": reason_category, "count": int(count)}
        for reason_category, count in reason_rows
    ]

    latency_rows = db.execute(
        select(McpScoreDispute.created_at, McpScoreDispute.resolved_at)
        .where(
            *conditions,
            McpScoreDispute.status == "resolved",
            McpScoreDispute.resolved_at.is_not(None),
        )
        .limit(1000)
    ).all()
    latencies = [
        _resolution_hours(created_at, resolved_at)
        for created_at, resolved_at in latency_rows
        if created_at is not None and resolved_at is not None
    ]
    median_resolution_hours = round(float(median(latencies)), 2) if latencies else None

    rows = db.execute(
        select(McpScoreDispute, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(*conditions)
        .order_by(McpScoreDispute.created_at.desc(), McpScoreDispute.id.desc())
        .limit(1000)
    ).all()

    disputes = [
        {
            "id": dispute.id,
            "server_id": dispute.server_id,
            "server_name": server.name if server is not None else None,
            "proposed_overall_risk": dispute.proposed_overall_risk,
            "current_risk_tier": server.risk_tier if server is not None else None,
            "current_verdict": server.verdict if server is not None else None,
            "reason_category": dispute.reason_category,
            "status": dispute.status,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at,
        }
        for dispute, server in rows
    ]

    return {
        "status_filter": selected_status,
        "days": days,
        "total": total,
        "by_status": by_status,
        "by_reason": by_reason,
        "median_resolution_hours": median_resolution_hours,
        "disputes": disputes,
    }


def build_dispute_resolution_ledger(
    db: Session,
    status_filter: str = "all",
    days: int = 30,
) -> Dict[str, Any]:
    """Compatibility wrapper for callers using the build_* convention."""
    return get_dispute_resolution_ledger(db, status_filter=status_filter, days=days)


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
    McpServerRegistry.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-alpha",
                    name="Alpha Server",
                    risk_tier="high",
                    verdict="rejected",
                ),
                McpServerRegistry(
                    server_id="server-beta",
                    name="Beta Server",
                    risk_tier="medium",
                    verdict="accepted",
                ),
                McpServerRegistry(
                    server_id="server-gamma",
                    name="Gamma Server",
                    risk_tier="low",
                    verdict="accepted",
                ),
            ]
        )
        session.add_all(
            [
                McpScoreDispute(
                    server_id="server-alpha",
                    submitted_by="analyst-one",
                    proposed_overall_risk=0.9,
                    reason_category="risk_disagreement",
                    explanation="Disputed high risk classification",
                    status="open",
                    created_at=now - timedelta(hours=2),
                ),
                McpScoreDispute(
                    server_id="server-beta",
                    submitted_by="analyst-two",
                    proposed_overall_risk=0.5,
                    reason_category="axis_disagreement",
                    explanation="Axis scores disagree with proposed risk",
                    status="open",
                    created_at=now - timedelta(hours=3),
                ),
                McpScoreDispute(
                    server_id="server-gamma",
                    submitted_by="analyst-three",
                    proposed_overall_risk=0.2,
                    reason_category="risk_disagreement",
                    explanation="Resolved risk classification dispute",
                    status="resolved",
                    created_at=now - timedelta(hours=8),
                    resolved_at=now - timedelta(hours=4),
                ),
                McpScoreDispute(
                    server_id="server-alpha",
                    submitted_by="analyst-four",
                    proposed_overall_risk=0.8,
                    reason_category="evidence_disagreement",
                    explanation="Resolved evidence classification dispute",
                    status="resolved",
                    created_at=now - timedelta(hours=12),
                    resolved_at=now - timedelta(hours=10),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/disputes/ledger")
    def ledger(
        status: str = "all",
        days: int = 30,
        db: Session = Depends(get_session),
    ):
        return get_dispute_resolution_ledger(db, status_filter=status, days=days)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get(
        "/api/disputes/ledger",
        params={"status": "all", "days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["by_status"] == {"open": 2, "resolved": 2}
    assert payload["by_reason"]
    assert any(
        dispute["server_name"] == "Alpha Server"
        for dispute in payload["disputes"]
    )
    print("PASS")