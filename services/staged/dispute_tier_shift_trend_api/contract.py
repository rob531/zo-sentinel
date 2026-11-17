from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


TIER_ORDER = ["trusted", "low", "moderate", "high", "critical"]


class SummaryResponse(BaseModel):
    open: int
    accepted: int
    rejected: int
    pending: int


class ReasonCountResponse(BaseModel):
    reason_category: str
    count: int


class ShiftResponse(BaseModel):
    date: str
    direction: str
    count: int


class TierShiftTrendResponse(BaseModel):
    days: int
    generated_at: str
    summary: SummaryResponse
    by_reason: List[ReasonCountResponse]
    shifts: List[ShiftResponse]


router = APIRouter()


def _status_category(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"accepted", "accept", "approved"}:
        return "accepted"
    if status in {"rejected", "reject", "denied"}:
        return "rejected"
    if status in {"pending", "submitted", "review"}:
        return "pending"
    return "open"


def _tier_index(value: object) -> Optional[int]:
    if value is None:
        return None
    tier = str(value).strip().lower()
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return None


def _direction(proposed: object, current: object) -> str:
    proposed_index = _tier_index(proposed)
    current_index = _tier_index(current)
    if proposed_index is None or current_index is None:
        return "same"
    if proposed_index > current_index:
        return "up"
    if proposed_index < current_index:
        return "down"
    return "same"


@router.get(
    "/disputes/tier-shift-trend",
    response_model=TierShiftTrendResponse,
)
def tier_shift_trend(
    days: int = Query(default=30, ge=1, le=365),
    status: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> TierShiftTrendResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.created_at >= cutoff)
        .order_by(McpScoreDispute.created_at, McpScoreDispute.id)
    )

    if status is not None:
        statement = statement.where(
            func.lower(McpScoreDispute.status) == status.strip().lower()
        )

    rows = session.execute(statement).all()

    summary = {"open": 0, "accepted": 0, "rejected": 0, "pending": 0}
    reason_counts: Dict[str, int] = {}
    shift_counts: Dict[str, Dict[str, int]] = {}

    for row in rows:
        summary[_status_category(row.status)] += 1

        reason = str(row.reason_category or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        event_at = row.resolved_at or row.created_at
        event_date = event_at.date().isoformat()
        direction = _direction(row.proposed_overall_risk, row.risk_tier)
        daily = shift_counts.setdefault(
            event_date, {"up": 0, "down": 0, "same": 0}
        )
        daily[direction] += 1

    by_reason = [
        ReasonCountResponse(reason_category=reason, count=count)
        for reason, count in sorted(
            reason_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    shifts = [
        ShiftResponse(date=event_date, direction=direction, count=counts[direction])
        for event_date, counts in sorted(shift_counts.items())
        for direction in ("up", "down", "same")
        if counts[direction]
    ]

    return TierShiftTrendResponse(
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=SummaryResponse(**summary),
        by_reason=by_reason,
        shifts=shifts,
    )


service_app = FastAPI(title="dispute_tier_shift_trend_api")
service_app.include_router(router, prefix="/api")


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_one = now - timedelta(days=1)
    day_two = now - timedelta(days=2)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE mcp_server_registry ("
                "server_id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "risk_tier TEXT"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE mcp_score_disputes ("
                "id INTEGER PRIMARY KEY, "
                "server_id TEXT NOT NULL, "
                "proposed_overall_risk TEXT, "
                "proposed_axes TEXT, "
                "reason_category TEXT, "
                "status TEXT, "
                "created_at DATETIME, "
                "resolved_at DATETIME"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, risk_tier) "
                "VALUES (:server_id, :name, :risk_tier)"
            ),
            [
                {"server_id": "srv-up", "name": "Up", "risk_tier": "trusted"},
                {"server_id": "srv-down", "name": "Down", "risk_tier": "critical"},
                {"server_id": "srv-same", "name": "Same", "risk_tier": "moderate"},
                {"server_id": "srv-pending", "name": "Pending", "risk_tier": "moderate"},
            ],
        )
        connection.execute(
            text(
                "INSERT INTO mcp_score_disputes "
                "(id, server_id, proposed_overall_risk, proposed_axes, "
                "reason_category, status, created_at, resolved_at) "
                "VALUES (:id, :server_id, :proposed_overall_risk, :proposed_axes, "
                ":reason_category, :status, :created_at, :resolved_at)"
            ),
            [
                {
                    "id": 1,
                    "server_id": "srv-up",
                    "proposed_overall_risk": "moderate",
                    "proposed_axes": "{}",
                    "reason_category": "policy_update",
                    "status": "accepted",
                    "created_at": day_one,
                    "resolved_at": day_one,
                },
                {
                    "id": 2,
                    "server_id": "srv-down",
                    "proposed_overall_risk": "high",
                    "proposed_axes": "{}",
                    "reason_category": "false_positive",
                    "status": "rejected",
                    "created_at": day_one,
                    "resolved_at": day_one,
                },
                {
                    "id": 3,
                    "server_id": "srv-same",
                    "proposed_overall_risk": "moderate",
                    "proposed_axes": "{}",
                    "reason_category": "policy_update",
                    "status": "open",
                    "created_at": day_two,
                    "resolved_at": None,
                },
                {
                    "id": 4,
                    "server_id": "srv-pending",
                    "proposed_overall_risk": "moderate",
                    "proposed_axes": "{}",
                    "reason_category": "new_evidence",
                    "status": "pending",
                    "created_at": day_two,
                    "resolved_at": None,
                },
            ],
        )

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/tier-shift-trend")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["pending"] == 1, payload
        assert sum(shift["count"] for shift in payload["shifts"]) == 4, payload
        assert any(shift["direction"] == "up" for shift in payload["shifts"]), payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()