import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/disputes", tags=["disputes"])


class StatusCount(BaseModel):
    status: str
    count: int


class ReasonCategoryCount(BaseModel):
    reason_category: str
    count: int


class TierShiftCount(BaseModel):
    from_tier: str
    to_tier: str
    count: int


class DisputeOutcomeMixResponse(BaseModel):
    days: int
    by_status: List[StatusCount]
    by_reason_category: List[ReasonCategoryCount]
    tier_shift: List[TierShiftCount]
    median_resolution_hours: Optional[float]
    generated_at: str


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_dispute_outcome_mix(session: Session, days: int = 30) -> DisputeOutcomeMixResponse:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)

    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.created_at >= cutoff)
    )

    rows = session.execute(statement).mappings().all()

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    tier_shift_counts: Counter[tuple[str, str]] = Counter()
    resolution_hours: List[float] = []

    for row in rows:
        status = str(row["status"])
        reason_category = str(row["reason_category"])
        status_counts[status] += 1
        reason_counts[reason_category] += 1

        from_tier = row["risk_tier"]
        to_tier = row["proposed_overall_risk"]
        if from_tier is not None and to_tier is not None:
            from_tier = str(from_tier)
            to_tier = str(to_tier)
            if from_tier != to_tier:
                tier_shift_counts[(from_tier, to_tier)] += 1

        created_at = row["created_at"]
        resolved_at = row["resolved_at"]
        if created_at is not None and resolved_at is not None:
            elapsed_hours = (
                _utc_datetime(resolved_at) - _utc_datetime(created_at)
            ).total_seconds() / 3600.0
            if math.isfinite(elapsed_hours) and elapsed_hours >= 0:
                resolution_hours.append(elapsed_hours)

    median_resolution_hours = (
        float(median(resolution_hours)) if resolution_hours else None
    )

    return DisputeOutcomeMixResponse(
        days=days,
        by_status=[
            StatusCount(status=status, count=count)
            for status, count in sorted(status_counts.items())
        ],
        by_reason_category=[
            ReasonCategoryCount(reason_category=reason, count=count)
            for reason, count in sorted(reason_counts.items())
        ],
        tier_shift=[
            TierShiftCount(from_tier=from_tier, to_tier=to_tier, count=count)
            for (from_tier, to_tier), count in sorted(
                tier_shift_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        median_resolution_hours=median_resolution_hours,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/outcome-mix", response_model=DisputeOutcomeMixResponse)
def get_dispute_outcome_mix(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> DisputeOutcomeMixResponse:
    return compute_dispute_outcome_mix(session=session, days=days)


if __name__ == "__main__":
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
    TestingSessionLocal = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    reason_category VARCHAR NOT NULL,
                    proposed_overall_risk VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, risk_tier)
                VALUES (:server_id, :risk_tier)
                """
            ),
            [
                {"server_id": "server-a", "risk_tier": "CAUTION_LIMITED"},
                {"server_id": "server-b", "risk_tier": "TRUSTED_GENERAL"},
            ],
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id,
                    server_id,
                    status,
                    reason_category,
                    proposed_overall_risk,
                    created_at,
                    resolved_at
                )
                VALUES (
                    :id,
                    :server_id,
                    :status,
                    :reason_category,
                    :proposed_overall_risk,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-a",
                    "status": "pending",
                    "reason_category": "axis_disagreement",
                    "proposed_overall_risk": "TRUSTED_GENERAL",
                    "created_at": now - timedelta(hours=5),
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": "server-a",
                    "status": "pending",
                    "reason_category": "axis_disagreement",
                    "proposed_overall_risk": "TRUSTED_GENERAL",
                    "created_at": now - timedelta(hours=8),
                    "resolved_at": None,
                },
                {
                    "id": 3,
                    "server_id": "server-b",
                    "status": "pending",
                    "reason_category": "policy_mismatch",
                    "proposed_overall_risk": "CAUTION_LIMITED",
                    "created_at": now - timedelta(hours=10),
                    "resolved_at": None,
                },
                {
                    "id": 4,
                    "server_id": "server-a",
                    "status": "resolved",
                    "reason_category": "policy_mismatch",
                    "proposed_overall_risk": "TRUSTED_GENERAL",
                    "created_at": now - timedelta(hours=60),
                    "resolved_at": now - timedelta(hours=20),
                },
                {
                    "id": 5,
                    "server_id": "server-b",
                    "status": "resolved",
                    "reason_category": "axis_disagreement",
                    "proposed_overall_risk": "CAUTION_LIMITED",
                    "created_at": now - timedelta(hours=100),
                    "resolved_at": now - timedelta(hours=50),
                },
            ],
        )

    def override_get_session():
        test_session = TestingSessionLocal()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/outcome-mix", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        actual_status_counts = {
            item["status"]: item["count"] for item in payload["by_status"]
        }
        assert actual_status_counts == {"pending": 3, "resolved": 2}
        assert payload["median_resolution_hours"] is not None

    print("PASS")