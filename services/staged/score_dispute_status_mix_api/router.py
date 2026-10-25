from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_dispute_status_mix as _get_status_mix
except ImportError:
    from .logic import get_status_mix as _get_status_mix


class ReasonCategoryCount(BaseModel):
    reason_category: str
    count: int


class DisputeStatusMixResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason_category: List[ReasonCategoryCount]
    median_resolution_hours: float
    unresolved_oldest_created_at: Optional[datetime]


router = APIRouter(prefix="/api")


@router.get(
    "/disputes/status-mix",
    response_model=DisputeStatusMixResponse,
    summary="Get score-dispute status and reason-category mix",
)
async def dispute_status_mix(
    status: Optional[str] = Query(
        None,
        description="Optional status filter.",
    ),
    limit: int = Query(
        500,
        description="Maximum number of dispute rows to inspect.",
    ),
    db: Session = Depends(get_session),
) -> Any:
    effective_limit = min(max(limit, 1), 500)
    result = _get_status_mix(db, status, effective_limit)
    if inspect.isawaitable(result):
        result = await result
    return result


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
    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
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
                    explanation TEXT,
                    status TEXT,
                    admin_note TEXT,
                    created_at TEXT,
                    resolved_at TEXT
                )
                """
            )
        )

        seed_rows = [
            (
                1,
                10,
                100,
                72.5,
                '{"harm": 70, "truthfulness": 75}',
                "model_quality",
                "Quality mismatch",
                "open",
                None,
                "2024-01-01T00:00:00",
                None,
            ),
            (
                2,
                10,
                101,
                61.0,
                '{"harm": 60, "truthfulness": 62}',
                "model_quality",
                "Quality mismatch",
                "open",
                None,
                "2024-01-03T00:00:00",
                None,
            ),
            (
                3,
                11,
                102,
                48.0,
                '{"harm": 45, "truthfulness": 51}',
                "policy_violation",
                "Policy concern",
                "in_review",
                None,
                "2024-01-02T00:00:00",
                None,
            ),
            (
                4,
                11,
                103,
                35.0,
                '{"harm": 30, "truthfulness": 40}',
                "policy_violation",
                "Policy concern",
                "resolved",
                "Adjusted after review",
                "2024-01-04T00:00:00",
                "2024-01-04T02:00:00",
            ),
            (
                5,
                12,
                104,
                20.0,
                '{"harm": 18, "truthfulness": 22}',
                "other",
                "Other reason",
                "rejected",
                "No violation found",
                "2024-01-05T00:00:00",
                "2024-01-05T01:00:00",
            ),
        ]

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
                    explanation,
                    status,
                    admin_note,
                    created_at,
                    resolved_at
                ) VALUES (
                    :id,
                    :server_id,
                    :submitted_by,
                    :proposed_overall_risk,
                    :proposed_axes,
                    :reason_category,
                    :explanation,
                    :status,
                    :admin_note,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "id": row[0],
                    "server_id": row[1],
                    "submitted_by": row[2],
                    "proposed_overall_risk": row[3],
                    "proposed_axes": row[4],
                    "reason_category": row[5],
                    "explanation": row[6],
                    "status": row[7],
                    "admin_note": row[8],
                    "created_at": row[9],
                    "resolved_at": row[10],
                }
                for row in seed_rows
            ],
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/status-mix")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["total"] == 5
        assert payload["by_status"] == {
            "open": 2,
            "in_review": 1,
            "resolved": 1,
            "rejected": 1,
        }
        assert isinstance(payload["median_resolution_hours"], float)
        assert payload["median_resolution_hours"] >= 0.0

        reason_counts = {
            item["reason_category"]: item["count"]
            for item in payload["by_reason_category"]
        }
        assert reason_counts == {
            "model_quality": 2,
            "policy_violation": 2,
            "other": 1,
        }

        oldest = payload["unresolved_oldest_created_at"]
        assert isinstance(oldest, str)
        datetime.fromisoformat(oldest.replace("Z", "+00:00"))

    test_engine.dispose()
    print("PASS")