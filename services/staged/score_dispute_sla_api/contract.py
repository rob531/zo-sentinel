from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class DisputeSlaResponse(BaseModel):
    days: int
    by_status: Dict[str, int]
    by_reason: Dict[str, int]
    median_resolution_hours: Optional[float]
    p90_resolution_hours: Optional[float]
    overdue_open: int


DISPUTE_STATUSES = (
    "open",
    "under_review",
    "approved",
    "rejected",
    "escalated",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def get_dispute_sla_summary(session: Session, days: int) -> DisputeSlaResponse:
    rows = list(session.execute(select(McpScoreDispute)).scalars().all())
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    by_status = {status: 0 for status in DISPUTE_STATUSES}
    by_reason: Dict[str, int] = {}
    resolution_hours: list[float] = []
    overdue_open = 0

    for dispute in rows:
        if dispute.created_at is None:
            continue
        created_at = _utc(dispute.created_at)
        if created_at < cutoff:
            continue

        status = (dispute.status or "unknown").strip().lower()
        by_status[status] = by_status.get(status, 0) + 1

        reason = (dispute.reason_category or "unspecified").strip() or "unspecified"
        by_reason[reason] = by_reason.get(reason, 0) + 1

        if dispute.resolved_at is not None:
            resolved_at = _utc(dispute.resolved_at)
            elapsed = (resolved_at - created_at).total_seconds() / 3600.0
            if elapsed >= 0:
                resolution_hours.append(elapsed)
        elif created_at < datetime.now(timezone.utc) - timedelta(hours=72):
            overdue_open += 1

    return DisputeSlaResponse(
        days=days,
        by_status=by_status,
        by_reason=by_reason,
        median_resolution_hours=(
            float(median(resolution_hours)) if resolution_hours else None
        ),
        p90_resolution_hours=_percentile(resolution_hours, 0.90),
        overdue_open=overdue_open,
    )


router = APIRouter(prefix="/api", tags=["score-disputes"])


@router.get("/disputes/sla", response_model=DisputeSlaResponse)
def dispute_sla(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> DisputeSlaResponse:
    return get_dispute_sla_summary(session=session, days=days)


app = FastAPI(title="Score Dispute SLA API")
app.include_router(router)


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        McpScoreDispute.__table__.create(bind=engine)
    except SQLAlchemyError:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE mcp_score_disputes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_id INTEGER,
                        submitted_by VARCHAR,
                        proposed_overall_risk FLOAT,
                        proposed_axes TEXT,
                        reason_category VARCHAR,
                        explanation TEXT,
                        status VARCHAR,
                        admin_note TEXT,
                        created_at DATETIME,
                        resolved_at DATETIME
                    )
                    """
                )
            )

    now = datetime.now(timezone.utc)
    seed_rows = [
        McpScoreDispute(
            server_id=1,
            submitted_by="contract-user",
            proposed_overall_risk=0.8,
            proposed_axes={"security": 0.8},
            reason_category="model_quality",
            explanation="Approved after review",
            status="approved",
            admin_note="Reviewed",
            created_at=now - timedelta(hours=3),
            resolved_at=now - timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="contract-user",
            proposed_overall_risk=0.6,
            proposed_axes={"quality": 0.6},
            reason_category="data_issue",
            explanation="Rejected after review",
            status="rejected",
            admin_note="Reviewed",
            created_at=now - timedelta(hours=102),
            resolved_at=now - timedelta(hours=100),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="contract-user",
            proposed_overall_risk=0.7,
            proposed_axes={"policy": 0.7},
            reason_category="policy_mismatch",
            explanation="Escalated for review",
            status="escalated",
            admin_note=None,
            created_at=now - timedelta(hours=200),
            resolved_at=now - timedelta(hours=100),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="contract-user",
            proposed_overall_risk=0.5,
            proposed_axes={"security": 0.5},
            reason_category="model_quality",
            explanation="Awaiting review",
            status="open",
            admin_note=None,
            created_at=now - timedelta(hours=100),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by="contract-user",
            proposed_overall_risk=0.4,
            proposed_axes={"quality": 0.4},
            reason_category="data_issue",
            explanation="Awaiting review",
            status="open",
            admin_note=None,
            created_at=now - timedelta(hours=80),
            resolved_at=None,
        ),
    ]

    with TestingSession() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/sla", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 30
        assert payload["by_status"] == {
            "open": 2,
            "under_review": 0,
            "approved": 1,
            "rejected": 1,
            "escalated": 1,
        }
        assert payload["by_reason"] == {
            "model_quality": 2,
            "data_issue": 2,
            "policy_mismatch": 1,
        }
        assert payload["overdue_open"] == 2
        assert payload["median_resolution_hours"] is not None
        assert payload["median_resolution_hours"] > 0
        assert payload["p90_resolution_hours"] is not None
        assert payload["p90_resolution_hours"] > 0

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()