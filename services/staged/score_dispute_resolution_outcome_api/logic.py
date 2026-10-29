from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_RESOLVED_STATUSES = {"RESOLVED", "REJECTED"}
_HIGHER_RISK_VALUES = {"HIGH", "CRITICAL", "SEVERE", "ELEVATED"}


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (TypeError, ValueError):
        return None


def _resolution_days(created_at: Any, resolved_at: Any) -> float | None:
    created = _as_datetime(created_at)
    resolved = _as_datetime(resolved_at)
    if created is None or resolved is None:
        return None
    return max(0.0, (resolved - created).total_seconds() / 86400.0)


def get_resolution_outcomes(session: Session, days: int = 30) -> dict[str, Any]:
    """Return resolution outcomes grouped by dispute category and status."""
    try:
        lookback_days = max(0, int(days))
    except (TypeError, ValueError):
        lookback_days = 30

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=lookback_days
    )

    statement = (
        select(
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpScoreDispute.proposed_overall_risk,
        )
        .where(McpScoreDispute.created_at >= cutoff)
        .where(McpScoreDispute.resolved_at.is_not(None))
    )

    rows = session.execute(statement).all()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for reason_category, status, created_at, resolved_at, proposed_risk in rows:
        normalized_status = str(status or "UNKNOWN").upper()
        if normalized_status not in _RESOLVED_STATUSES:
            continue

        category_name = str(reason_category or "UNKNOWN")
        key = (category_name, normalized_status)
        bucket = grouped.setdefault(
            key,
            {
                "reason_category": category_name,
                "status": normalized_status,
                "dispute_count": 0,
                "resolution_days": [],
                "raised_risk_count": 0,
            },
        )

        bucket["dispute_count"] += 1
        elapsed = _resolution_days(created_at, resolved_at)
        if elapsed is not None:
            bucket["resolution_days"].append(elapsed)

        risk = str(proposed_risk or "").strip().upper()
        if risk in _HIGHER_RISK_VALUES:
            bucket["raised_risk_count"] += 1

    categories: list[dict[str, Any]] = []
    for key in sorted(grouped):
        bucket = grouped[key]
        durations = bucket["resolution_days"]
        categories.append(
            {
                "reason_category": bucket["reason_category"],
                "status": bucket["status"],
                "dispute_count": bucket["dispute_count"],
                "mean_resolution_days": round(
                    sum(durations) / len(durations), 4
                )
                if durations
                else 0.0,
                "max_resolution_days": round(max(durations), 4)
                if durations
                else 0.0,
                "raised_risk_count": bucket["raised_risk_count"],
            }
        )

    return {
        "days": lookback_days,
        "categories": categories,
        "total": sum(category["dispute_count"] for category in categories),
    }


def get_dispute_resolution_outcomes(
    session: Session, days: int = 30
) -> dict[str, Any]:
    return get_resolution_outcomes(session, days)


def compute_resolution_outcomes(
    session: Session, days: int = 30
) -> dict[str, Any]:
    return get_resolution_outcomes(session, days)


__all__ = [
    "get_resolution_outcomes",
    "get_dispute_resolution_outcomes",
    "compute_resolution_outcomes",
]


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)

    test_session = Session(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    test_session.add_all(
        [
            McpScoreDispute(
                server_id=1,
                submitted_by=1,
                proposed_overall_risk="HIGH",
                proposed_axes={},
                reason_category="Access Control",
                explanation="First dispute",
                status="RESOLVED",
                admin_note="Accepted",
                created_at=now - timedelta(days=12),
                resolved_at=now - timedelta(days=10),
            ),
            McpScoreDispute(
                server_id=2,
                submitted_by=1,
                proposed_overall_risk="LOW",
                proposed_axes={},
                reason_category="Access Control",
                explanation="Second dispute",
                status="RESOLVED",
                admin_note="Accepted",
                created_at=now - timedelta(days=15),
                resolved_at=now - timedelta(days=10),
            ),
            McpScoreDispute(
                server_id=3,
                submitted_by=1,
                proposed_overall_risk="HIGH",
                proposed_axes={},
                reason_category="Data Exposure",
                explanation="Third dispute",
                status="REJECTED",
                admin_note="Rejected",
                created_at=now - timedelta(days=18),
                resolved_at=now - timedelta(days=15),
            ),
            McpScoreDispute(
                server_id=4,
                submitted_by=1,
                proposed_overall_risk="MEDIUM",
                proposed_axes={},
                reason_category="Data Exposure",
                explanation="Fourth dispute",
                status="REJECTED",
                admin_note="Rejected",
                created_at=now - timedelta(days=20),
                resolved_at=now - timedelta(days=15),
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/disputes/resolution-outcomes")
    def resolution_outcomes(days: int = 30):
        return get_resolution_outcomes(test_session, days)

    test_app.dependency_overrides[get_session] = lambda: test_session
    client = TestClient(test_app)
    response = client.get("/api/disputes/resolution-outcomes?days=30")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 4
    assert any(
        category["dispute_count"] == 2
        and category["mean_resolution_days"] == 3.5
        for category in payload["categories"]
    )
    print("PASS")