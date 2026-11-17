from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


class ReasonCategoryMix(BaseModel):
    reason_category: str
    count: int
    open_count: int
    resolved_count: int
    avg_resolution_hours: Optional[float]


class ReasonMixTotals(BaseModel):
    disputes: int
    categories: int
    open: int


class ReasonMixResponse(BaseModel):
    categories: List[ReasonCategoryMix]
    totals: ReasonMixTotals
    generated_at: str


router = APIRouter(prefix="/api")


def _build_reason_mix(rows) -> ReasonMixResponse:
    grouped: Dict[str, Dict[str, object]] = {}

    for row in rows:
        category = row.reason_category
        status = row.status
        bucket = grouped.setdefault(
            category,
            {
                "count": 0,
                "open_count": 0,
                "resolved_count": 0,
                "resolution_hours": [],
            },
        )
        bucket["count"] += 1
        if status not in ("resolved", "rejected"):
            bucket["open_count"] += 1
        if status == "resolved":
            bucket["resolved_count"] += 1
        if row.created_at is not None and row.resolved_at is not None:
            bucket["resolution_hours"].append(
                (row.resolved_at - row.created_at).total_seconds() / 3600.0
            )

    categories = []
    for category, bucket in grouped.items():
        resolution_hours = bucket["resolution_hours"]
        average = (
            round(sum(resolution_hours) / len(resolution_hours), 2)
            if resolution_hours
            else None
        )
        categories.append(
            ReasonCategoryMix(
                reason_category=category,
                count=bucket["count"],
                open_count=bucket["open_count"],
                resolved_count=bucket["resolved_count"],
                avg_resolution_hours=average,
            )
        )

    categories.sort(key=lambda item: (-item.count, item.reason_category))

    return ReasonMixResponse(
        categories=categories,
        totals=ReasonMixTotals(
            disputes=sum(item.count for item in categories),
            categories=len(categories),
            open=sum(item.open_count for item in categories),
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/disputes/reason-mix", response_model=ReasonMixResponse)
def get_dispute_reason_mix(session: Session = Depends(get_session)) -> ReasonMixResponse:
    rows = session.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
    ).all()
    return _build_reason_mix(rows)


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    reason_category VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )
        created_at = datetime(2025, 1, 1, 12, 0, 0)
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes
                    (id, reason_category, status, created_at, resolved_at)
                VALUES
                    (:id, :reason_category, :status, :created_at, :resolved_at)
                """
            ),
            [
                {
                    "id": 1,
                    "reason_category": "axis_mislabel",
                    "status": "open",
                    "created_at": created_at,
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "reason_category": "axis_mislabel",
                    "status": "open",
                    "created_at": created_at,
                    "resolved_at": None,
                },
                {
                    "id": 3,
                    "reason_category": "missing_evidence",
                    "status": "resolved",
                    "created_at": created_at,
                    "resolved_at": created_at.replace(hour=14),
                },
            ],
        )

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/reason-mix")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["totals"]["disputes"] == 3
        assert payload["totals"]["categories"] == 2
        assert payload["totals"]["open"] == 2

        categories = {
            item["reason_category"]: item for item in payload["categories"]
        }
        assert categories["axis_mislabel"]["count"] == 2
        assert categories["axis_mislabel"]["open_count"] == 2
        assert categories["missing_evidence"]["resolved_count"] == 1
        assert categories["missing_evidence"]["avg_resolution_hours"] == 2.0

    print("PASS")


if __name__ == "__main__":
    run()