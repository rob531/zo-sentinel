"""logic.py -- dispute ageing histogram data and computation layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


class AgeingBucket(BaseModel):
    label: str
    min_days: int
    max_days: Optional[int]
    count: int


class StatusAgeSummary(BaseModel):
    status: str
    count: int
    avg_age_days: float


class AgeingHistogramResponse(BaseModel):
    buckets: List[AgeingBucket]
    by_status: List[StatusAgeSummary]
    total: int
    overdue: int


_BUCKET_DEFINITIONS = (
    ("0-1 days", 0, 1),
    ("1-3 days", 1, 3),
    ("3-7 days", 3, 7),
    ("7-14 days", 7, 14),
    ("14-30 days", 14, 30),
    ("30+ days", 30, None),
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, resolved_at: Optional[datetime], now: datetime) -> float:
    start = _as_utc(created_at)
    end = _as_utc(resolved_at) if resolved_at is not None else now
    return max(0.0, (end - start).total_seconds() / 86400.0)


def _bucket_index(age_days: float) -> int:
    if age_days < 1:
        return 0
    if age_days < 3:
        return 1
    if age_days < 7:
        return 2
    if age_days < 14:
        return 3
    if age_days < 30:
        return 4
    return 5


def get_ageing_histogram(
    db: Session,
    status: Optional[str] = None,
    buckets: int = 6,
) -> Dict[str, object]:
    """Compute the dispute ageing histogram from the real application table."""
    now = datetime.now(timezone.utc)
    query = select(
        McpScoreDispute.status,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    )
    if status is not None:
        query = query.where(McpScoreDispute.status == status)

    rows = db.execute(query).all()
    bucket_counts = [0] * 6
    status_counts: Dict[str, int] = {}
    status_age_totals: Dict[str, float] = {}
    overdue = 0

    for row in rows:
        row_status = row.status or "unknown"
        age = _age_days(row.created_at, row.resolved_at, now)
        bucket_counts[_bucket_index(age)] += 1
        status_counts[row_status] = status_counts.get(row_status, 0) + 1
        status_age_totals[row_status] = status_age_totals.get(row_status, 0.0) + age
        if age > 30:
            overdue += 1

    bucket_output: List[Dict[str, object]] = []
    for (label, min_days, max_days), count in zip(_BUCKET_DEFINITIONS, bucket_counts):
        bucket_output.append(
            {
                "label": label,
                "min_days": min_days,
                "max_days": max_days,
                "count": int(count),
            }
        )

    by_status = [
        {
            "status": row_status,
            "count": int(status_counts[row_status]),
            "avg_age_days": float(
                status_age_totals[row_status] / status_counts[row_status]
            ),
        }
        for row_status in sorted(status_counts)
    ]

    return {
        "buckets": bucket_output,
        "by_status": by_status,
        "total": int(len(rows)),
        "overdue": int(overdue),
    }


def get_dispute_ageing_histogram(
    db: Session,
    status: Optional[str] = None,
    buckets: int = 6,
) -> Dict[str, object]:
    """Compatibility entry point for the dispute ageing endpoint."""
    return get_ageing_histogram(db, status=status, buckets=buckets)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        db.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-1",
                    submitted_by="analyst-1",
                    proposed_overall_risk=0.9,
                    reason_category="score_disagreement",
                    status="open",
                    created_at=now - timedelta(days=40),
                    explanation="Forty-day-old dispute",
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-2",
                    submitted_by="analyst-2",
                    proposed_overall_risk=0.5,
                    reason_category="score_disagreement",
                    status="open",
                    created_at=now - timedelta(days=2),
                    explanation="Two-day-old dispute",
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-3",
                    submitted_by="analyst-3",
                    proposed_overall_risk=0.7,
                    reason_category="score_disagreement",
                    status="resolved",
                    created_at=now - timedelta(days=10),
                    resolved_at=now - timedelta(days=5),
                    explanation="Resolved dispute",
                ),
                McpScoreDispute(
                    id=4,
                    server_id="server-4",
                    submitted_by="analyst-4",
                    proposed_overall_risk=0.2,
                    reason_category="score_disagreement",
                    status="rejected",
                    created_at=now - timedelta(days=1),
                    explanation="Rejected dispute",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get(
        "/api/disputes/ageing",
        response_model=AgeingHistogramResponse,
    )
    def ageing_endpoint(
        status: Optional[str] = None,
        buckets: int = 6,
        db: Session = Depends(get_session),
    ):
        return get_ageing_histogram(db, status=status, buckets=buckets)

    response = TestClient(that_app).get("/api/disputes/ageing")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 4
    assert payload["overdue"] == 1
    assert len(payload["buckets"]) == 6
    assert all(bucket["count"] >= 0 for bucket in payload["buckets"])
    print("PASS")