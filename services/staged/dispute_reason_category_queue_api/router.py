from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_dispute_reason_category_queue
except ImportError:
    from .logic import get_reason_category_queue as get_dispute_reason_category_queue


class DisputeReasonCategory(BaseModel):
    reason_category: str
    open: int
    resolved: int
    median_open_age_days: float | None = None
    proposed_tiers: dict[str, int] = Field(default_factory=dict)


class DisputeReasonCategoryQueueResponse(BaseModel):
    total_open: int
    categories: list[DisputeReasonCategory]


router = APIRouter(prefix="/api", tags=["disputes"])


@router.get(
    "/disputes/reason-categories",
    response_model=DisputeReasonCategoryQueueResponse,
)
def get_reason_categories(
    session: Session = Depends(get_session),
) -> DisputeReasonCategoryQueueResponse:
    result = get_dispute_reason_category_queue(session)

    if isinstance(result, tuple) and len(result) == 2:
        total_open, categories = result
    elif isinstance(result, Mapping) and "categories" in result:
        total_open = result.get("total_open")
        categories = result["categories"]
    elif isinstance(result, Mapping):
        total_open = None
        categories = [
            {"reason_category": reason_category, **values}
            for reason_category, values in result.items()
        ]
    else:
        total_open = None
        categories = result

    normalized: list[dict[str, Any]] = []
    for category in categories:
        item = dict(category)
        item.setdefault("proposed_tiers", {})
        item.setdefault("resolved", 0)
        item.setdefault("median_open_age_days", None)
        normalized.append(item)

    if total_open is None:
        total_open = sum(int(item.get("open", 0)) for item in normalized)

    return DisputeReasonCategoryQueueResponse(
        total_open=int(total_open),
        categories=normalized,
    )


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    table_name = McpScoreDispute.__tablename__

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    submitted_by VARCHAR,
                    proposed_overall_risk VARCHAR,
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
        rows = [
            (
                1,
                101,
                "analyst-1",
                "high",
                "[]",
                "billing",
                "Billing score disputed",
                "open",
                None,
                now - timedelta(days=1),
                None,
            ),
            (
                2,
                102,
                "analyst-2",
                "low",
                "[]",
                "billing",
                "Billing score disputed",
                "open",
                None,
                now - timedelta(days=3),
                None,
            ),
            (
                3,
                103,
                "analyst-3",
                "medium",
                "[]",
                "billing",
                "Billing score disputed",
                "resolved",
                "Reviewed",
                now - timedelta(days=5),
                now,
            ),
            (
                4,
                104,
                "analyst-4",
                "medium",
                "[]",
                "identity",
                "Identity score disputed",
                "open",
                None,
                now - timedelta(days=2),
                None,
            ),
            (
                5,
                105,
                "analyst-5",
                "high",
                "[]",
                "identity",
                "Identity score disputed",
                "rejected",
                "Invalid",
                now - timedelta(days=4),
                now,
            ),
            (
                6,
                106,
                "analyst-6",
                "low",
                "[]",
                "availability",
                "Availability score disputed",
                "open",
                None,
                now - timedelta(days=6),
                None,
            ),
        ]
        connection.execute(
            text(
                f"""
                INSERT INTO {table_name} (
                    id, server_id, submitted_by, proposed_overall_risk,
                    proposed_axes, reason_category, explanation, status,
                    admin_note, created_at, resolved_at
                ) VALUES (
                    :id, :server_id, :submitted_by, :proposed_overall_risk,
                    :proposed_axes, :reason_category, :explanation, :status,
                    :admin_note, :created_at, :resolved_at
                )
                """
            ),
            [
                dict(zip(
                    (
                        "id",
                        "server_id",
                        "submitted_by",
                        "proposed_overall_risk",
                        "proposed_axes",
                        "reason_category",
                        "explanation",
                        "status",
                        "admin_note",
                        "created_at",
                        "resolved_at",
                    ),
                    row,
                ))
                for row in rows
            ],
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/reason-categories")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_open"] == 4, payload
        assert len(payload["categories"]) >= 3, payload
        billing = next(
            category
            for category in payload["categories"]
            if category["reason_category"] == "billing"
        )
        assert billing["open"] == 2, billing
        assert billing["resolved"] == 1, billing
        assert billing["proposed_tiers"] == {"high": 1, "low": 1}, billing

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    from fastapi import FastAPI

    _self_test()