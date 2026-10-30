from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


router = APIRouter(prefix="/api", tags=["disputes"])


class DisputeStatusCount(BaseModel):
    status: str
    count: int
    pct: float


class DisputeReasonCount(BaseModel):
    reason_category: str
    count: int


class DisputeResolutionSummary(BaseModel):
    resolved_count: int
    open_count: int
    median_resolution_hours: Optional[float]


class DisputeOutcomeRollup(BaseModel):
    generated_at: str
    total: int
    by_status: List[DisputeStatusCount]
    by_reason: List[DisputeReasonCount]
    resolution: DisputeResolutionSummary
    oldest_open_age_days: float


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _elapsed_hours(start: Any, end: Any) -> Optional[float]:
    started = _as_datetime(start)
    ended = _as_datetime(end)
    if started is None or ended is None:
        return None
    if started.tzinfo is None and ended.tzinfo is not None:
        started = started.replace(tzinfo=ended.tzinfo)
    elif started.tzinfo is not None and ended.tzinfo is None:
        ended = ended.replace(tzinfo=started.tzinfo)
    return (ended - started).total_seconds() / 3600.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_dispute_outcomes(session: Session = Depends(get_session)) -> dict:
    rows = session.query(
        McpScoreDispute.status,
        McpScoreDispute.reason_category,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).all()

    total = len(rows)
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    resolution_hours: List[float] = []
    resolved_count = 0
    open_count = 0
    open_ages: List[float] = []

    now = _utc_now()

    for status, reason_category, created_at, resolved_at in rows:
        status_value = str(status) if status is not None else "unknown"
        reason_value = str(reason_category) if reason_category is not None else "unknown"

        status_counts[status_value] = status_counts.get(status_value, 0) + 1
        reason_counts[reason_value] = reason_counts.get(reason_value, 0) + 1

        if status_value == "open":
            open_count += 1
            created = _as_datetime(created_at)
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                open_ages.append(max(0.0, (now - created).total_seconds() / 86400.0))

        if status_value == "resolved" and resolved_at is not None:
            resolved_count += 1
            hours = _elapsed_hours(created_at, resolved_at)
            if hours is not None:
                resolution_hours.append(hours)

    by_status = [
        {
            "status": status,
            "count": count,
            "pct": round((count / total) * 100, 2) if total else 0.0,
        }
        for status, count in sorted(status_counts.items())
    ]
    by_reason = [
        {"reason_category": reason, "count": count}
        for reason, count in sorted(reason_counts.items())
    ]

    median_hours = round(median(resolution_hours), 2) if resolution_hours else None

    return {
        "generated_at": now.isoformat(),
        "total": total,
        "by_status": by_status,
        "by_reason": by_reason,
        "resolution": {
            "resolved_count": resolved_count,
            "open_count": open_count,
            "median_resolution_hours": median_hours,
        },
        "oldest_open_age_days": round(max(open_ages), 2) if open_ages else 0.0,
    }


@router.get("/disputes/outcomes", response_model=DisputeOutcomeRollup)
def dispute_outcomes(session: Session = Depends(get_session)) -> dict:
    return get_dispute_outcomes(session)


app = FastAPI(title="dispute_outcome_rollup_api")
app.include_router(router)


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
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    fixture = SessionLocal()
    fixture.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id=1,
                submitted_by="analyst",
                proposed_overall_risk=50,
                reason_category="model_mismatch",
                status="open",
                created_at=now,
                explanation="fixture",
            ),
            McpScoreDispute(
                id=2,
                server_id=1,
                submitted_by="analyst",
                proposed_overall_risk=60,
                reason_category="context_error",
                status="open",
                created_at=now,
                explanation="fixture",
            ),
            McpScoreDispute(
                id=3,
                server_id=1,
                submitted_by="analyst",
                proposed_overall_risk=70,
                reason_category="model_mismatch",
                status="resolved",
                created_at=now,
                resolved_at=now + __import__("datetime").timedelta(hours=48),
                explanation="fixture",
            ),
            McpScoreDispute(
                id=4,
                server_id=1,
                submitted_by="analyst",
                proposed_overall_risk=80,
                reason_category="policy_error",
                status="rejected",
                created_at=now,
                explanation="fixture",
            ),
        ]
    )
    fixture.commit()
    fixture.close()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/outcomes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["resolution"]["open_count"] == 2
    assert any(
        item["status"] == "open" and item["count"] == 2
        for item in payload["by_status"]
    )
    assert payload["resolution"]["median_resolution_hours"] == 48.0
    print("PASS")