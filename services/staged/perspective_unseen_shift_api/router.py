from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

from .logic import get_unseen_shifts as fetch_unseen_shifts


class UnseenByPerspective(BaseModel):
    perspective_id: int
    perspective_name: str
    unseen_count: int
    last_change_at: Optional[datetime] = None


class UnseenTierTransition(BaseModel):
    old_tier: str
    new_tier: str
    count: int


class TopUnseenServer(BaseModel):
    server_id: int
    unseen_count: int


class UnseenShiftsResponse(BaseModel):
    days: int
    total_events: int
    unseen_events: int
    unseen_by_perspective: List[UnseenByPerspective]
    unseen_tier_transitions: List[UnseenTierTransition]
    top_unseen_servers: List[TopUnseenServer]


router = APIRouter(prefix="/api", tags=["perspectives"])


@router.get(
    "/perspectives/unseen-shifts",
    response_model=UnseenShiftsResponse,
    summary="Summarize unseen perspective tier shifts",
)
def get_unseen_shifts(
    days: int = Query(default=30, description="Number of days to inspect"),
    limit: int = Query(default=50, description="Maximum rows per summary section"),
    session: Session = Depends(get_session),
) -> UnseenShiftsResponse:
    bounded_days = max(1, min(days, 365))
    bounded_limit = max(1, min(limit, 200))
    return fetch_unseen_shifts(session, bounded_days, bounded_limit)


def _self_test() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    now = datetime.utcnow()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    name VARCHAR NOT NULL,
                    description TEXT,
                    facet_filters TEXT,
                    created_by INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id INTEGER NOT NULL,
                    server_id INTEGER NOT NULL,
                    change_type VARCHAR NOT NULL,
                    old_tier VARCHAR,
                    new_tier VARCHAR,
                    seen BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO perspectives
                    (id, org_id, name, description, facet_filters, created_by, created_at, updated_at)
                VALUES
                    (1, 1, 'Primary', 'Primary perspective', '{}', 1, :now, :now),
                    (2, 1, 'Secondary', 'Secondary perspective', '{}', 1, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_events
                    (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
                VALUES
                    (1, 1, 10, 'tier_change', 'legacy', 'standard', 0, :now),
                    (2, 1, 10, 'tier_change', 'legacy', 'standard', 0, :now),
                    (3, 2, 20, 'tier_change', 'legacy', 'standard', 1, :now),
                    (4, 2, 21, 'tier_change', 'standard', 'current', 1, :now)
                """
            ),
            {"now": now},
        )

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    local_app = router

    def override_get_session():
        with session_factory() as session:
            yield session

    from fastapi import FastAPI

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/perspectives/unseen-shifts?days=30&limit=50")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_events"] == 4
            assert payload["unseen_events"] == 2
            assert len(payload["unseen_by_perspective"]) == 1
            assert payload["unseen_by_perspective"][0]["unseen_count"] == 2
            assert {
                (item["old_tier"], item["new_tier"], item["count"])
                for item in payload["unseen_tier_transitions"]
            } == {("legacy", "standard", 2)}
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()