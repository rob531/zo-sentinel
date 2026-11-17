"""Data and computation layer for dispute outcome rates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _cutoff(days: int) -> datetime:
    if days < 1:
        raise ValueError("days must be at least 1")
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _is_resolved(status: str | None, resolved_at: datetime | None) -> bool:
    if resolved_at is not None:
        return True
    normalized = (status or "").strip().lower()
    return any(
        marker in normalized
        for marker in ("resolved", "approv", "reject", "denied", "dismissed")
    )


def get_outcome_rates(db: Session, days: int = 30) -> Dict[str, object]:
    """Compute status, reason-category, and acceptance metrics for recent disputes."""
    cutoff = _cutoff(days)

    status_rows = db.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.resolved_at,
            func.count(McpScoreDispute.id),
        )
        .where(McpScoreDispute.created_at >= cutoff)
        .group_by(McpScoreDispute.status, McpScoreDispute.resolved_at)
    ).all()

    by_status: Dict[str, int] = {}
    resolved_total = 0
    resolved_approved = 0

    for status, resolved_at, count in status_rows:
        status_key = status or "unknown"
        count_value = int(count)
        by_status[status_key] = by_status.get(status_key, 0) + count_value

        if _is_resolved(status_key, resolved_at):
            resolved_total += count_value
            if "approv" in status_key.lower():
                resolved_approved += count_value

    reason_rows = db.execute(
        select(McpScoreDispute.reason_category, func.count(McpScoreDispute.id))
        .where(McpScoreDispute.created_at >= cutoff)
        .group_by(McpScoreDispute.reason_category)
    ).all()

    by_reason_category: Dict[str, int] = {}
    for reason, count in reason_rows:
        reason_key = reason or "unknown"
        by_reason_category[reason_key] = int(count)

    total = sum(by_status.values())
    accept_rate = (
        float(resolved_approved / resolved_total) if resolved_total else 0.0
    )

    return {
        "days": days,
        "total": total,
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "accept_rate": accept_rate,
    }


compute_outcome_rates = get_outcome_rates


def _run_self_test() -> None:
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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        McpScoreDispute(
            server_id="server-1",
            submitted_by="reviewer-1",
            proposed_overall_risk=0.8,
            explanation="Axis mismatch",
            reason_category="model_quality",
            status="pending",
            created_at=now - timedelta(hours=1),
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="reviewer-1",
            proposed_overall_risk=0.6,
            explanation="Risk tier mismatch",
            reason_category="risk_score",
            status="approved",
            created_at=now - timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id="server-3",
            submitted_by="reviewer-2",
            proposed_overall_risk=0.4,
            explanation="Axis mismatch",
            reason_category="model_quality",
            status="approved",
            created_at=now - timedelta(days=1, hours=1),
        ),
        McpScoreDispute(
            server_id="server-4",
            submitted_by="reviewer-2",
            proposed_overall_risk=0.7,
            explanation="Risk tier mismatch",
            reason_category="risk_score",
            status="rejected",
            created_at=now - timedelta(days=1, hours=2),
        ),
        McpScoreDispute(
            server_id="server-5",
            submitted_by="reviewer-1",
            proposed_overall_risk=0.3,
            explanation="Axis mismatch",
            reason_category="model_quality",
            status="pending",
            created_at=now - timedelta(days=1, hours=3),
        ),
    ]

    with TestingSession() as session:
        session.add_all(rows)
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/disputes/outcome-rates")
    def outcome_rates_endpoint(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> Dict[str, object]:
        result = get_outcome_rates(db, days)
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        return result

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/outcome-rates", params={"days": 2})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 5, payload
        assert payload["by_status"]["pending"] == 2, payload
        assert isinstance(payload["accept_rate"], float), payload
        assert 0.0 <= payload["accept_rate"] <= 1.0, payload

    print("PASS")


if __name__ == "__main__":
    _run_self_test()