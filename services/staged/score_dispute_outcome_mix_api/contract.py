from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


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


class OutcomeMixResponse(BaseModel):
    days: int
    by_status: List[StatusCount]
    by_reason_category: List[ReasonCategoryCount]
    tier_shift: List[TierShiftCount]
    median_resolution_hours: Optional[float]
    generated_at: str


router = APIRouter(prefix="/api")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _tier_label(value: object) -> Optional[str]:
    if value is None:
        return None
    label = str(value).strip()
    return label.upper() if label else None


@router.get("/disputes/outcome-mix", response_model=OutcomeMixResponse)
def get_outcome_mix(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> OutcomeMixResponse:
    cutoff = _utc_now() - timedelta(days=days)

    dispute_rows = session.execute(
        select(
            McpScoreDispute.server_id,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        ).where(McpScoreDispute.created_at >= cutoff)
    ).all()

    status_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    resolution_hours: List[float] = []
    server_ids = set()

    for server_id, status, reason_category, proposed_risk, created_at, resolved_at in dispute_rows:
        status_value = str(status or "unknown")
        reason_value = str(reason_category or "unknown")
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
        reason_counts[reason_value] = reason_counts.get(reason_value, 0) + 1
        server_ids.add(server_id)

        if resolved_at is not None and created_at is not None:
            duration = (_as_utc(resolved_at) - _as_utc(created_at)).total_seconds() / 3600.0
            if duration >= 0:
                resolution_hours.append(duration)

    registry_tiers: Dict[str, str] = {}
    if server_ids:
        registry_rows = session.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.risk_tier).where(
                McpServerRegistry.server_id.in_(server_ids)
            )
        ).all()
        for server_id, risk_tier in registry_rows:
            label = _tier_label(risk_tier)
            if label is not None:
                registry_tiers[server_id] = label

    tier_shift_counts: Dict[tuple, int] = {}
    for server_id, _, _, proposed_risk, _, _ in dispute_rows:
        from_tier = registry_tiers.get(server_id)
        to_tier = _tier_label(proposed_risk)
        if from_tier is None or to_tier is None or from_tier == to_tier:
            continue
        key = (from_tier, to_tier)
        tier_shift_counts[key] = tier_shift_counts.get(key, 0) + 1

    status_order = {"pending": 0, "resolved": 1, "rejected": 2}
    by_status = [
        StatusCount(status=status, count=count)
        for status, count in sorted(
            status_counts.items(),
            key=lambda item: (status_order.get(item[0], 3), item[0]),
        )
    ]
    by_reason_category = [
        ReasonCategoryCount(reason_category=reason, count=count)
        for reason, count in sorted(reason_counts.items())
    ]
    tier_shift = [
        TierShiftCount(from_tier=from_tier, to_tier=to_tier, count=count)
        for (from_tier, to_tier), count in sorted(tier_shift_counts.items())
    ]

    median_resolution_hours = (
        round(float(median(resolution_hours)), 3) if resolution_hours else None
    )

    return OutcomeMixResponse(
        days=days,
        by_status=by_status,
        by_reason_category=by_reason_category,
        tier_shift=tier_shift,
        median_resolution_hours=median_resolution_hours,
        generated_at=_utc_now().isoformat(),
    )


app = FastAPI(title="Score Dispute Outcome Mix API")
app.include_router(router)


def run_contract() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason_category TEXT NOT NULL,
                    proposed_overall_risk TEXT NOT NULL,
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
                    server_id TEXT PRIMARY KEY,
                    risk_tier TEXT NOT NULL
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        rows = [
            {
                "id": 1,
                "server_id": "server-a",
                "status": "pending",
                "reason_category": "axis_disagreement",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now - timedelta(hours=2),
                "resolved_at": None,
            },
            {
                "id": 2,
                "server_id": "server-a",
                "status": "pending",
                "reason_category": "coverage_gap",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now - timedelta(hours=3),
                "resolved_at": None,
            },
            {
                "id": 3,
                "server_id": "server-b",
                "status": "resolved",
                "reason_category": "axis_disagreement",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now - timedelta(hours=10),
                "resolved_at": now - timedelta(hours=9),
            },
            {
                "id": 4,
                "server_id": "server-b",
                "status": "resolved",
                "reason_category": "axis_disagreement",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now - timedelta(hours=20),
                "resolved_at": now - timedelta(hours=17),
            },
            {
                "id": 5,
                "server_id": "server-c",
                "status": "resolved",
                "reason_category": "coverage_gap",
                "proposed_overall_risk": "CAUTION_LIMITED",
                "created_at": now - timedelta(hours=30),
                "resolved_at": now - timedelta(hours=25),
            },
        ]
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
                ) VALUES (
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
            rows,
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
                {"server_id": "server-b", "risk_tier": "CAUTION_LIMITED"},
                {"server_id": "server-c", "risk_tier": "TRUSTED_GENERAL"},
            ],
        )

    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/outcome-mix", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        status_counts = {item["status"]: item["count"] for item in payload["by_status"]}
        assert status_counts == {"pending": 2, "resolved": 3}, payload
        assert payload["median_resolution_hours"] is not None, payload
        assert payload["days"] == 30, payload
        assert payload["generated_at"], payload

    print("PASS")


if __name__ == "__main__":
    run_contract()