from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def get_dispute_outcomes(
    days: int = 30,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return score-dispute counts and resolution statistics for a time window."""
    window_days = max(int(days), 0)
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    rows = session.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        ).where(
            McpScoreDispute.created_at >= cutoff
        )
    ).all()

    status_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    reason_resolution_hours: Dict[str, List[float]] = {}
    resolution_hours: List[float] = []
    resolved_count = 0

    for status, reason_category, created_at, resolved_at in rows:
        status_key = status if status is not None else "unknown"
        reason_key = reason_category if reason_category is not None else "unknown"

        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1

        if resolved_at is not None and created_at is not None:
            latency = (resolved_at - created_at).total_seconds() / 3600.0
            resolution_hours.append(latency)
            reason_resolution_hours.setdefault(reason_key, []).append(latency)
            resolved_count += 1

    by_reason = [
        {
            "reason_category": reason,
            "count": count,
            "mean_resolution_hours": (
                sum(reason_resolution_hours.get(reason, []))
                / len(reason_resolution_hours[reason])
                if reason_resolution_hours.get(reason)
                else None
            ),
        }
        for reason, count in sorted(reason_counts.items())
    ]

    return {
        "days": window_days,
        "status_counts": status_counts,
        "mean_resolution_hours": (
            sum(resolution_hours) / len(resolution_hours)
            if resolution_hours
            else None
        ),
        "resolved_count": resolved_count,
        "open_count": len(rows) - resolved_count,
        "by_reason": by_reason,
    }


# Keep common naming variants available to routers while sharing one implementation.
get_score_dispute_outcomes = get_dispute_outcomes
get_dispute_outcome_summary = get_dispute_outcomes
get_outcome_summary = get_dispute_outcomes
get_outcomes = get_dispute_outcomes


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine)
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    now = datetime.utcnow()
    local_session.add_all(
        [
            McpScoreDispute(
                server_id="server-1",
                submitted_by="user-1",
                proposed_overall_risk="high",
                proposed_axes={},
                reason_category="model_mismatch",
                explanation="",
                status="resolved",
                admin_note="",
                created_at=now - timedelta(days=2),
                resolved_at=now - timedelta(days=2) + timedelta(hours=2),
            ),
            McpScoreDispute(
                server_id="server-2",
                submitted_by="user-2",
                proposed_overall_risk="medium",
                proposed_axes={},
                reason_category="model_mismatch",
                explanation="",
                status="resolved",
                admin_note="",
                created_at=now - timedelta(days=3),
                resolved_at=now - timedelta(days=3) + timedelta(hours=4),
            ),
            McpScoreDispute(
                server_id="server-3",
                submitted_by="user-3",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="missing_evidence",
                explanation="",
                status="open",
                admin_note="",
                created_at=now - timedelta(days=1),
                resolved_at=None,
            ),
            McpScoreDispute(
                server_id="server-4",
                submitted_by="user-4",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="other",
                explanation="",
                status="pending",
                admin_note="",
                created_at=now - timedelta(days=1),
                resolved_at=None,
            ),
        ]
    )
    local_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/disputes/outcomes")
    def outcomes(days: int = 30):
        return get_dispute_outcomes(days=days, session=local_session)

    that_app.dependency_overrides[get_session] = lambda: local_session
    response = TestClient(that_app).get("/api/disputes/outcomes?days=7")
    payload = response.json()

    assert response.status_code == 200
    assert payload["resolved_count"] == 2
    assert abs(payload["mean_resolution_hours"] - 3.0) < 0.001
    assert any(
        item["reason_category"] == "model_mismatch" and item["count"] == 2
        for item in payload["by_reason"]
    )
    print("PASS")