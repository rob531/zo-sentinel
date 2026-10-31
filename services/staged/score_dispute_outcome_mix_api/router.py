from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

try:
    from .logic import calculate_median_resolution_hours
except ImportError:

    def calculate_median_resolution_hours(values: list[float]) -> Optional[float]:
        if not values:
            return None
        return round(float(median(values)), 6)


router = APIRouter(prefix="/api")


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
    by_status: list[StatusCount]
    by_reason_category: list[ReasonCategoryCount]
    tier_shift: list[TierShiftCount]
    median_resolution_hours: Optional[float]
    generated_at: str


def _parse_timestamp(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get(
    "/disputes/outcome-mix",
    response_model=DisputeOutcomeMixResponse,
)
def get_dispute_outcome_mix(
    days: int = Query(default=30, ge=1),
    session=Depends(get_session),
) -> DisputeOutcomeMixResponse:
    cutoff = datetime.now(timezone.utc).timestamp() - days * 24 * 60 * 60

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

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    resolution_hours: list[float] = []
    server_ids: set[str] = set()

    for server_id, status, reason_category, _proposed_tier, created_at, resolved_at in dispute_rows:
        status_counts[str(status)] += 1
        if reason_category is not None:
            reason_counts[str(reason_category)] += 1
        if server_id is not None:
            server_ids.add(str(server_id))

        created = _parse_timestamp(created_at)
        resolved = _parse_timestamp(resolved_at)
        if created is not None and resolved is not None:
            duration = (resolved - created).total_seconds() / 3600
            if duration >= 0:
                resolution_hours.append(duration)

    tier_by_server: dict[str, str] = {}
    if server_ids:
        try:
            registry_rows = session.execute(
                select(
                    McpServerRegistry.server_id,
                    McpServerRegistry.risk_tier,
                ).where(McpServerRegistry.server_id.in_(server_ids))
            ).all()
            tier_by_server = {
                str(server_id): str(risk_tier)
                for server_id, risk_tier in registry_rows
                if server_id is not None and risk_tier is not None
            }
        except SQLAlchemyError:
            session.rollback()

    tier_shift_counts: Counter[tuple[str, str]] = Counter()
    for server_id, _status, _reason_category, proposed_tier, _created_at, _resolved_at in dispute_rows:
        current_tier = tier_by_server.get(str(server_id))
        if current_tier is None or proposed_tier is None:
            continue
        proposed_tier = str(proposed_tier)
        if current_tier.casefold() != proposed_tier.casefold():
            tier_shift_counts[(current_tier, proposed_tier)] += 1

    status_order = {"pending": 0, "resolved": 1, "rejected": 2}
    ordered_statuses = sorted(
        status_counts,
        key=lambda value: (status_order.get(value, 10), value),
    )

    session.rollback()

    return DisputeOutcomeMixResponse(
        days=days,
        by_status=[
            StatusCount(status=status, count=status_counts[status])
            for status in ordered_statuses
        ],
        by_reason_category=[
            ReasonCategoryCount(reason_category=category, count=count)
            for category, count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        tier_shift=[
            TierShiftCount(from_tier=from_tier, to_tier=to_tier, count=count)
            for (from_tier, to_tier), count in sorted(
                tier_shift_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ],
        median_resolution_hours=calculate_median_resolution_hours(resolution_hours),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine)
    testing_session = Session()

    with engine.begin() as connection:
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
                    resolved_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            {
                "id": 1,
                "server_id": "server-a",
                "status": "pending",
                "reason_category": "axis_disagreement",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now,
                "resolved_at": None,
            },
            {
                "id": 2,
                "server_id": "server-a",
                "status": "pending",
                "reason_category": "axis_disagreement",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now,
                "resolved_at": None,
            },
            {
                "id": 3,
                "server_id": "server-b",
                "status": "pending",
                "reason_category": "policy_exception",
                "proposed_overall_risk": "CAUTION_LIMITED",
                "created_at": now,
                "resolved_at": None,
            },
            {
                "id": 4,
                "server_id": "server-a",
                "status": "resolved",
                "reason_category": "axis_disagreement",
                "proposed_overall_risk": "TRUSTED_GENERAL",
                "created_at": now,
                "resolved_at": now,
            },
            {
                "id": 5,
                "server_id": "server-b",
                "status": "resolved",
                "reason_category": "policy_exception",
                "proposed_overall_risk": "CAUTION_LIMITED",
                "created_at": now,
                "resolved_at": now,
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
            ],
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/disputes/outcome-mix", params={"days": 30})
            assert response.status_code == 200
            payload = response.json()
            status_counts = {
                item["status"]: item["count"] for item in payload["by_status"]
            }
            assert status_counts == {"pending": 3, "resolved": 2}
            assert payload["median_resolution_hours"] is not None
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()