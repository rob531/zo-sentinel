from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api")


class DisputeOutcomeMixResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_reason_category: dict[str, int]
    with_admin_note: int
    without_admin_note: int
    median_resolution_hours: float | None
    resolved_count: int
    open_count: int


def _resolution_hours(created_at: datetime, resolved_at: datetime) -> float:
    def normalized(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    delta = normalized(resolved_at) - normalized(created_at)
    return float(delta.total_seconds() / 3600.0)


@router.get("/disputes/outcomes", response_model=DisputeOutcomeMixResponse)
def get_dispute_outcomes(
    status: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=0),
    session: Any = Depends(get_session),
) -> DisputeOutcomeMixResponse:
    statement = select(McpScoreDispute)

    if status is not None:
        statement = statement.where(func.lower(McpScoreDispute.status) == status.strip().lower())

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        statement = statement.where(McpScoreDispute.created_at >= cutoff)

    rows = list(session.scalars(statement).all())

    by_status: dict[str, int] = {}
    by_reason_category: dict[str, int] = {}
    resolution_hours: list[float] = []
    with_admin_note = 0
    resolved_count = 0

    for row in rows:
        status_key = (row.status or "unknown").strip() or "unknown"
        reason_key = (row.reason_category or "unknown").strip() or "unknown"
        by_status[status_key] = by_status.get(status_key, 0) + 1
        by_reason_category[reason_key] = by_reason_category.get(reason_key, 0) + 1

        if row.admin_note is not None and row.admin_note.strip():
            with_admin_note += 1

        if status_key.casefold() == "resolved":
            resolved_count += 1
            if row.created_at is not None and row.resolved_at is not None:
                resolution_hours.append(_resolution_hours(row.created_at, row.resolved_at))

    median_resolution_hours = (
        float(median(resolution_hours)) if resolution_hours else None
    )

    return DisputeOutcomeMixResponse(
        total=len(rows),
        by_status=by_status,
        by_reason_category=by_reason_category,
        with_admin_note=with_admin_note,
        without_admin_note=len(rows) - with_admin_note,
        median_resolution_hours=median_resolution_hours,
        resolved_count=resolved_count,
        open_count=len(rows) - resolved_count,
    )


app = FastAPI(title="Dispute Resolution Outcome Mix API")
app.include_router(router)


def _self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    created_at = datetime(2025, 1, 1, 12, 0, 0)
    seeded_rows = [
        McpScoreDispute(
            server_id="server-a",
            submitted_by="user-a",
            proposed_overall_risk="low",
            proposed_axes={"quality": 0.9},
            reason_category="model_variance",
            explanation="First resolved dispute",
            status="resolved",
            admin_note="Reviewed",
            created_at=created_at,
            resolved_at=created_at + timedelta(hours=1),
        ),
        McpScoreDispute(
            server_id="server-b",
            submitted_by="user-b",
            proposed_overall_risk="medium",
            proposed_axes={"quality": 0.7},
            reason_category="data_quality",
            explanation="Second resolved dispute",
            status="resolved",
            admin_note="Adjusted",
            created_at=created_at,
            resolved_at=created_at + timedelta(hours=3),
        ),
        McpScoreDispute(
            server_id="server-c",
            submitted_by="user-c",
            proposed_overall_risk="high",
            proposed_axes={"quality": 0.4},
            reason_category="model_variance",
            explanation="Resolved without a note",
            status="resolved",
            admin_note=None,
            created_at=created_at,
            resolved_at=created_at + timedelta(hours=5),
        ),
        McpScoreDispute(
            server_id="server-d",
            submitted_by="user-d",
            proposed_overall_risk="low",
            proposed_axes={"quality": 0.8},
            reason_category="data_quality",
            explanation="Pending dispute",
            status="pending",
            admin_note=None,
            created_at=created_at,
            resolved_at=None,
        ),
    ]

    with test_session_factory() as session:
        session.add_all(seeded_rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/disputes/outcomes")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 4
            assert payload["resolved_count"] == 3
            assert payload["open_count"] == 1
            assert isinstance(payload["median_resolution_hours"], float)
            assert payload["median_resolution_hours"] >= 0
    finally:
        that_app.dependency_overrides.clear()
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()