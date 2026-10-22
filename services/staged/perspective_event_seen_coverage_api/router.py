from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

from .logic import get_event_seen_coverage

router = APIRouter(prefix="/api")


class PerspectiveCoverage(BaseModel):
    perspective_id: int
    name: str
    events: int
    seen: int
    unseen: int
    coverage_pct: float


class EventSeenCoverageResponse(BaseModel):
    days: int
    perspectives: List[PerspectiveCoverage]
    overall_seen_pct: float


@router.get(
    "/perspectives/event-seen-coverage",
    response_model=EventSeenCoverageResponse,
)
def event_seen_coverage(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> EventSeenCoverageResponse:
    result = get_event_seen_coverage(session, days)
    return EventSeenCoverageResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
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
                    server_id INTEGER,
                    change_type TEXT,
                    old_tier TEXT,
                    new_tier TEXT,
                    seen BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text("INSERT INTO perspectives (id, name) VALUES (:id, :name)"),
            {"id": 1, "name": "Seeded Perspective"},
        )
        connection.execute(
            text("INSERT INTO perspectives (id, name) VALUES (:id, :name)"),
            {"id": 2, "name": "Empty Perspective"},
        )
        for event_id, seen in enumerate((1, 1, 1, 0), start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO perspective_events (
                        id, perspective_id, server_id, change_type,
                        old_tier, new_tier, seen, created_at
                    ) VALUES (
                        :id, :perspective_id, :server_id, :change_type,
                        :old_tier, :new_tier, :seen, :created_at
                    )
                    """
                ),
                {
                    "id": event_id,
                    "perspective_id": 1,
                    "server_id": event_id,
                    "change_type": "tier_change",
                    "old_tier": "low",
                    "new_tier": "medium",
                    "seen": seen,
                    "created_at": now - timedelta(minutes=event_id),
                },
            )

    def override_get_session():
        database = testing_session()
        try:
            yield database
        finally:
            database.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/event-seen-coverage?days=30")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["perspectives"][0]["events"] == 4
        assert payload["perspectives"][0]["seen"] == 3
        assert payload["perspectives"][0]["unseen"] == 1
        assert payload["perspectives"][0]["coverage_pct"] == 75.0
        assert payload["perspectives"][1]["events"] == 0
        assert payload["perspectives"][1]["coverage_pct"] == 0.0

    print("PASS")