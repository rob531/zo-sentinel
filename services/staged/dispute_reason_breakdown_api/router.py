from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from .logic import get_dispute_reason_breakdown

router = APIRouter(prefix="/api")


class ReasonCategoryCount(BaseModel):
    reason_category: Optional[str]
    count: int


class StatusCount(BaseModel):
    status: Optional[str]
    count: int


class DisputeReasonBreakdownResponse(BaseModel):
    total: int
    by_reason_category: List[ReasonCategoryCount]
    by_status: List[StatusCount]
    unresolved_count: int
    median_resolution_hours: Optional[float]


@router.get(
    "/disputes/reasons",
    response_model=DisputeReasonBreakdownResponse,
)
def dispute_reason_breakdown(
    session: AsyncSession = Depends(get_session),
) -> DisputeReasonBreakdownResponse:
    return get_dispute_reason_breakdown(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    submitted_by INTEGER,
                    proposed_overall_risk REAL,
                    proposed_axes TEXT,
                    reason_category TEXT,
                    status TEXT,
                    created_at TIMESTAMP,
                    resolved_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id,
                    server_id,
                    submitted_by,
                    proposed_overall_risk,
                    proposed_axes,
                    reason_category,
                    status,
                    created_at,
                    resolved_at
                ) VALUES (
                    :id,
                    :server_id,
                    :submitted_by,
                    :proposed_overall_risk,
                    :proposed_axes,
                    :reason_category,
                    :status,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": 101,
                    "submitted_by": 1,
                    "proposed_overall_risk": 75.0,
                    "proposed_axes": '{"axis_a": 75}',
                    "reason_category": "security",
                    "status": "open",
                    "created_at": "2025-01-01T00:00:00",
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": 102,
                    "submitted_by": 1,
                    "proposed_overall_risk": 80.0,
                    "proposed_axes": '{"axis_a": 80}',
                    "reason_category": "security",
                    "status": "open",
                    "created_at": "2025-01-01T01:00:00",
                    "resolved_at": None,
                },
                {
                    "id": 3,
                    "server_id": 103,
                    "submitted_by": 2,
                    "proposed_overall_risk": 60.0,
                    "proposed_axes": '{"axis_a": 60}',
                    "reason_category": "quality",
                    "status": "resolved",
                    "created_at": "2025-01-01T00:00:00",
                    "resolved_at": "2025-01-01T02:00:00",
                },
                {
                    "id": 4,
                    "server_id": 104,
                    "submitted_by": 2,
                    "proposed_overall_risk": 65.0,
                    "proposed_axes": '{"axis_a": 65}',
                    "reason_category": "policy",
                    "status": "open",
                    "created_at": "2025-01-01T00:00:00",
                    "resolved_at": None,
                },
                {
                    "id": 5,
                    "server_id": 105,
                    "submitted_by": 3,
                    "proposed_overall_risk": 70.0,
                    "proposed_axes": '{"axis_a": 70}',
                    "reason_category": "policy",
                    "status": "resolved",
                    "created_at": "2025-01-01T00:00:00",
                    "resolved_at": "2025-01-01T04:00:00",
                },
            ],
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/reasons")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert any(
        item["reason_category"] == "security" and item["count"] == 2
        for item in payload["by_reason_category"]
    )
    assert payload["unresolved_count"] == 3
    print("PASS")