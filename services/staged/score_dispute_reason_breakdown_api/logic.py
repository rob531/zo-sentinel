from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_TERMINAL_DISPUTE_STATUSES = {"resolved", "rejected", "closed"}


def _normalize_created_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return float((ordered[midpoint - 1] + ordered[midpoint]) / 2.0)


def _calculate_reason_breakdown(session: Session) -> Dict[str, Any]:
    rows = session.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
        )
    ).all()

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    open_ages: List[float] = []
    now = datetime.now(timezone.utc)

    for status, reason_category, created_at in rows:
        status_key = str(status) if status is not None else "unknown"
        reason_key = str(reason_category) if reason_category is not None else "unknown"
        status_counts[status_key] += 1
        reason_counts[reason_key] += 1

        if status_key.strip().lower() not in _TERMINAL_DISPUTE_STATUSES:
            created = _normalize_created_at(created_at)
            if created is not None:
                age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
                open_ages.append(age_hours)

    return {
        "total": len(rows),
        "by_status": dict(status_counts),
        "by_reason": dict(reason_counts),
        "open_count": len(open_ages),
        "median_open_age_hours": _median(open_ages),
    }


def get_reason_breakdown(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _calculate_reason_breakdown(session)


get_dispute_reason_breakdown = get_reason_breakdown
get_score_dispute_reason_breakdown = get_reason_breakdown


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    reason_category VARCHAR,
                    status VARCHAR,
                    proposed_overall_risk VARCHAR,
                    created_at DATETIME,
                    resolved_at DATETIME,
                    proposed_axes TEXT,
                    admin_note TEXT,
                    explanation TEXT,
                    submitted_by INTEGER
                )
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_session = TestSession()
    base_time = datetime.now(timezone.utc) - timedelta(hours=4)
    test_rows = [
        McpScoreDispute(
            id=1,
            server_id=101,
            reason_category="false_positive",
            status="open",
            proposed_overall_risk="low",
            created_at=base_time,
        ),
        McpScoreDispute(
            id=2,
            server_id=102,
            reason_category="missing_evidence",
            status="pending",
            proposed_overall_risk="medium",
            created_at=base_time + timedelta(hours=1),
        ),
        McpScoreDispute(
            id=3,
            server_id=103,
            reason_category="policy_mismatch",
            status="open",
            proposed_overall_risk="high",
            created_at=base_time + timedelta(hours=2),
        ),
        McpScoreDispute(
            id=4,
            server_id=104,
            reason_category="false_positive",
            status="open",
            proposed_overall_risk="medium",
            created_at=base_time + timedelta(hours=3),
        ),
        McpScoreDispute(
            id=5,
            server_id=105,
            reason_category="missing_evidence",
            status="resolved",
            proposed_overall_risk="low",
            created_at=base_time,
            resolved_at=base_time + timedelta(hours=1),
        ),
        McpScoreDispute(
            id=6,
            server_id=106,
            reason_category="policy_mismatch",
            status="rejected",
            proposed_overall_risk="low",
            created_at=base_time,
        ),
    ]
    test_session.add_all(test_rows)
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/reason-breakdown")
    def reason_breakdown(result: Dict[str, Any] = Depends(get_reason_breakdown)):
        return result

    response = TestClient(test_app).get("/api/disputes/reason-breakdown")
    response.raise_for_status()
    payload = response.json()
    assert payload["total"] == 6
    assert sum(payload["by_status"].values()) == 6
    assert sum(payload["by_reason"].values()) == 6
    assert payload["open_count"] == 4
    assert isinstance(payload["median_open_age_hours"], float)
    assert payload["median_open_age_hours"] >= 0.0
    print("PASS")