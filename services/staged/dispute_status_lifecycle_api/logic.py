from datetime import datetime, timezone
from statistics import median
from typing import Dict, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class DisputeStatusLifecycleResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason: Dict[str, int]
    unresolved: int
    median_unresolved_age_hours: Optional[float]
    resolved_with_timestamp: int
    avg_resolution_hours: Optional[float]


router = APIRouter()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dispute_lifecycle(db: Session) -> DisputeStatusLifecycleResponse:
    total = int(db.execute(select(func.count()).select_from(McpScoreDispute)).scalar_one())

    status_rows = db.execute(
        select(McpScoreDispute.status, func.count())
        .group_by(McpScoreDispute.status)
    ).all()
    by_status: Dict[str, int] = {}
    for status, count in status_rows:
        key = status if status is not None else "unknown"
        by_status[key] = by_status.get(key, 0) + int(count)

    reason_rows = db.execute(
        select(McpScoreDispute.reason_category, func.count())
        .group_by(McpScoreDispute.reason_category)
    ).all()
    by_reason: Dict[str, int] = {}
    for reason, count in reason_rows:
        key = reason if reason is not None else "unknown"
        by_reason[key] = by_reason.get(key, 0) + int(count)

    unresolved_rows = db.execute(
        select(McpScoreDispute.created_at)
        .where(
            or_(
                McpScoreDispute.status.is_(None),
                McpScoreDispute.status.notin_(("resolved", "rejected")),
            )
        )
    ).all()
    now = datetime.now(timezone.utc)
    unresolved_ages = [
        max(0.0, (now - _as_utc(created_at)).total_seconds() / 3600.0)
        for (created_at,) in unresolved_rows
        if created_at is not None
    ]
    unresolved = len(unresolved_rows)
    median_unresolved_age_hours = (
        float(median(unresolved_ages)) if unresolved_ages else None
    )

    resolved_rows = db.execute(
        select(McpScoreDispute.created_at, McpScoreDispute.resolved_at).where(
            McpScoreDispute.status == "resolved",
            McpScoreDispute.resolved_at.is_not(None),
        )
    ).all()
    resolution_hours = [
        max(
            0.0,
            (_as_utc(resolved_at) - _as_utc(created_at)).total_seconds() / 3600.0,
        )
        for created_at, resolved_at in resolved_rows
        if created_at is not None and resolved_at is not None
    ]
    resolved_with_timestamp = len(resolved_rows)
    avg_resolution_hours = (
        sum(resolution_hours) / len(resolution_hours)
        if resolution_hours
        else None
    )

    return DisputeStatusLifecycleResponse(
        total=total,
        by_status=by_status,
        by_reason=by_reason,
        unresolved=unresolved,
        median_unresolved_age_hours=median_unresolved_age_hours,
        resolved_with_timestamp=resolved_with_timestamp,
        avg_resolution_hours=avg_resolution_hours,
    )


@router.get(
    "/disputes/lifecycle",
    response_model=DisputeStatusLifecycleResponse,
)
def get_dispute_status_lifecycle(
    db: Session = Depends(get_session),
) -> DisputeStatusLifecycleResponse:
    return _dispute_lifecycle(db)


def create_app() -> FastAPI:
    application = FastAPI(title="Dispute Status Lifecycle API")
    application.include_router(router, prefix="/api")
    return application


app = create_app()


if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    created_at = datetime.now(timezone.utc) - timedelta(hours=4)
    test_db.add_all(
        [
            McpScoreDispute(
                server_id=1,
                submitted_by="test-user",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="model_disagreement",
                explanation="test dispute",
                status="open",
                created_at=created_at,
            ),
            McpScoreDispute(
                server_id=1,
                submitted_by="test-user",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="missing_evidence",
                explanation="test dispute",
                status="open",
                created_at=created_at,
            ),
            McpScoreDispute(
                server_id=1,
                submitted_by="test-user",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="policy_mismatch",
                explanation="test dispute",
                status="open",
                created_at=created_at,
            ),
            McpScoreDispute(
                server_id=1,
                submitted_by="test-user",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="model_disagreement",
                explanation="test dispute",
                status="resolved",
                created_at=created_at,
                resolved_at=created_at + timedelta(hours=2),
            ),
            McpScoreDispute(
                server_id=1,
                submitted_by="test-user",
                proposed_overall_risk=50.0,
                proposed_axes={},
                reason_category="model_disagreement",
                explanation="test dispute",
                status="rejected",
                created_at=created_at,
                resolved_at=None,
            ),
        ]
    )
    test_db.commit()
    test_db.close()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/disputes/lifecycle")
    payload = response.json()

    assert response.status_code == 200
    assert payload["by_status"]["open"] == 3
    assert payload["unresolved"] == 3
    assert payload["resolved_with_timestamp"] == 1
    assert isinstance(payload["median_unresolved_age_hours"], float)
    assert payload["median_unresolved_age_hours"] >= 0
    assert payload["total"] == 5

    print("PASS")