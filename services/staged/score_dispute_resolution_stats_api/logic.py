from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter()

STATUS_KEYS = ("open", "in_review", "resolved", "rejected", "escalated")


class DisputeAgingStats(BaseModel):
    oldest_open_days: int
    median_open_age_days: float


class DisputeStatsResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason_category: Dict[str, int]
    by_proposed_tier: Dict[str, int]
    aging: DisputeAgingStats
    generated_at: str


def _normalize_status(value: object) -> str:
    status = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return status


def _tier_key(value: object) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or "unknown"


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/disputes/stats", response_model=DisputeStatsResponse)
def get_dispute_stats(
    days: int = Query(default=30, ge=0),
    session=Depends(get_session),
) -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = (
        session.query(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
        .filter(
            McpScoreDispute.created_at >= cutoff,
            McpScoreDispute.created_at <= now,
        )
        .all()
    )

    by_status = {status: 0 for status in STATUS_KEYS}
    by_reason_category: Dict[str, int] = {}
    by_proposed_tier: Dict[str, int] = {}
    open_ages: list[float] = []

    for status_value, reason_category, proposed_tier, created_at, _resolved_at in rows:
        status = _normalize_status(status_value)
        if status in by_status:
            by_status[status] += 1

        reason_key = str(reason_category or "uncategorized").strip() or "uncategorized"
        by_reason_category[reason_key] = by_reason_category.get(reason_key, 0) + 1

        tier_key = _tier_key(proposed_tier)
        by_proposed_tier[tier_key] = by_proposed_tier.get(tier_key, 0) + 1

        if status == "open" and created_at is not None:
            age_seconds = max(0.0, (now - _utc_datetime(created_at)).total_seconds())
            open_ages.append(age_seconds / 86400.0)

    oldest_open_days = int(max(open_ages)) if open_ages else 0
    median_open_age_days = float(median(open_ages)) if open_ages else 0.0

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "by_proposed_tier": by_proposed_tier,
        "aging": {
            "oldest_open_days": oldest_open_days,
            "median_open_age_days": median_open_age_days,
        },
        "generated_at": now.isoformat(),
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine, checkfirst=True)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    id_column = McpScoreDispute.__table__.columns["id"]
    risk_column = McpScoreDispute.__table__.columns["proposed_overall_risk"]

    def make_id(index: int) -> object:
        try:
            python_type = id_column.type.python_type
        except (AttributeError, NotImplementedError):
            python_type = str
        if python_type is int:
            return index
        if python_type.__name__ == "UUID":
            return uuid4()
        return str(uuid4())

    def make_risk(index: int) -> object:
        try:
            python_type = risk_column.type.python_type
        except (AttributeError, NotImplementedError):
            python_type = str
        if python_type is int:
            return index
        if python_type is float:
            return float(index)
        return f"tier_{index}"

    first_created_at = datetime.now(timezone.utc) - timedelta(days=1)
    second_created_at = datetime.now(timezone.utc) - timedelta(days=10)
    statuses = ("open", "in_review", "resolved")
    reasons = ("scoring", "data_quality", "policy")

    with test_session_factory() as test_session:
        for index in range(6):
            status = statuses[index % len(statuses)]
            test_session.add(
                McpScoreDispute(
                    id=make_id(index + 1),
                    server_id="server-test",
                    submitted_by="user-test",
                    explanation="Test dispute",
                    proposed_overall_risk=make_risk((index % 3) + 1),
                    proposed_axes={},
                    reason_category=reasons[index % len(reasons)],
                    status=status,
                    created_at=first_created_at if index % 2 == 0 else second_created_at,
                    resolved_at=datetime.now(timezone.utc) if status == "resolved" else None,
                )
            )
        test_session.commit()

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")

    def override_get_session():
        with test_session_factory() as test_session:
            yield test_session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/stats")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 6
        assert set(payload["by_status"]) == set(STATUS_KEYS)
        assert all(isinstance(count, int) for count in payload["by_status"].values())
        assert payload["aging"]["oldest_open_days"] >= 0

    print("PASS")