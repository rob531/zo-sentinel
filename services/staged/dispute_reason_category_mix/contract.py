from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


class ReasonCategoryStatusCounts(BaseModel):
    reason_category: str
    by_status: dict[str, int]
    open_count: int
    oldest_open_days: int


class ReasonCategoryMixResponse(BaseModel):
    categories: list[ReasonCategoryStatusCounts]
    total_open: int
    total: int


def _age_days(created_at: datetime, as_of: date) -> int:
    return max(0, (as_of - created_at.date()).days)


def _reason_category_mix(db: Session, as_of: Optional[date]) -> ReasonCategoryMixResponse:
    reference_date = as_of or date.today()
    rows = db.execute(
        select(
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
        )
    ).all()

    grouped: dict[str, dict[str, list[datetime]]] = defaultdict(
        lambda: {"open": [], "resolved": [], "rejected": []}
    )

    for reason_category, status, created_at in rows:
        normalized_status = str(status or "open").lower()
        if normalized_status not in grouped[reason_category]:
            grouped[reason_category][normalized_status] = []
        grouped[reason_category][normalized_status].append(created_at)

    categories: list[ReasonCategoryStatusCounts] = []
    total_open = 0

    for reason_category in sorted(grouped):
        status_groups = grouped[reason_category]
        by_status = {
            status: len(status_groups.get(status, []))
            for status in ("open", "resolved", "rejected")
        }
        open_count = by_status["open"]
        total_open += open_count

        oldest_open_days = max(
            (_age_days(created_at, reference_date) for created_at in status_groups["open"]),
            default=0,
        )

        categories.append(
            ReasonCategoryStatusCounts(
                reason_category=reason_category,
                by_status=by_status,
                open_count=open_count,
                oldest_open_days=oldest_open_days,
            )
        )

    return ReasonCategoryMixResponse(
        categories=categories,
        total_open=total_open,
        total=len(rows),
    )


def _build_test_app(db: Session) -> FastAPI:
    test_app = FastAPI()

    @test_app.get(
        "/api/disputes/reason-category-mix",
        response_model=ReasonCategoryMixResponse,
    )
    def reason_category_mix(
        as_of: Optional[date] = None,
        session: Session = Depends(get_session),
    ) -> ReasonCategoryMixResponse:
        return _reason_category_mix(session, as_of)

    test_app.dependency_overrides[get_session] = lambda: db
    return test_app


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    db = test_session_factory()

    try:
        McpScoreDispute.__table__.create(engine, checkfirst=True)

        db.add_all(
            [
                McpScoreDispute(
                    explanation="Contract open category alpha",
                    proposed_overall_risk=0.8,
                    reason_category="category_alpha",
                    server_id=1,
                    submitted_by="contract@example.test",
                    status="open",
                    created_at=datetime(2024, 1, 1, 12, 0, 0),
                ),
                McpScoreDispute(
                    explanation="Contract resolved category alpha",
                    proposed_overall_risk=0.6,
                    reason_category="category_alpha",
                    server_id=1,
                    submitted_by="contract@example.test",
                    status="resolved",
                    created_at=datetime(2024, 1, 2, 12, 0, 0),
                    resolved_at=datetime(2024, 1, 4, 12, 0, 0),
                ),
                McpScoreDispute(
                    explanation="Contract open category beta",
                    proposed_overall_risk=0.4,
                    reason_category="category_beta",
                    server_id=2,
                    submitted_by="contract@example.test",
                    status="open",
                    created_at=datetime(2024, 1, 3, 12, 0, 0),
                ),
            ]
        )
        db.commit()

        with TestClient(_build_test_app(db)) as client:
            response = client.get(
                "/api/disputes/reason-category-mix",
                params={"as_of": "2024-01-11"},
            )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["categories"]) == 2
        assert payload["total_open"] == 2
        assert payload["total"] == 3

        categories = {
            category["reason_category"]: category for category in payload["categories"]
        }
        assert categories["category_alpha"]["by_status"] == {
            "open": 1,
            "resolved": 1,
            "rejected": 0,
        }
        assert categories["category_alpha"]["oldest_open_days"] == 10
        assert categories["category_beta"]["by_status"] == {
            "open": 1,
            "resolved": 0,
            "rejected": 0,
        }
        assert categories["category_beta"]["oldest_open_days"] == 8
    finally:
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()