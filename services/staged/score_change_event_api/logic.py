from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class ScoreChangeEvent(BaseModel):
    event_time: datetime
    server_id: str
    server_name: Optional[str] = None
    axis: Optional[str] = None
    field: Optional[str] = None
    old_value: Any = None
    new_value: Any = None
    delta: Any = None


class ScoreChangesResponse(BaseModel):
    since_days: int
    count: int
    events: list[ScoreChangeEvent]


def get_score_changes(
    server_id: Optional[str] = None,
    since_days: int = 30,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> ScoreChangesResponse:
    bounded_since_days = max(0, int(since_days))
    bounded_limit = min(500, max(1, int(limit)))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=bounded_since_days)

    conditions = ["e.event_time >= :cutoff"]
    parameters: dict[str, Any] = {
        "cutoff": cutoff,
        "limit": bounded_limit,
    }

    if server_id is not None:
        conditions.append("e.server_id = :server_id")
        parameters["server_id"] = server_id

    sql = text(
        """
        SELECT
            e.event_time,
            e.server_id,
            r.name AS server_name,
            e.name AS field,
            e.old_value,
            e.new_value,
            e.delta,
            e.axis
        FROM score_change_events AS e
        INNER JOIN mcp_server_registry AS r
            ON r.server_id = e.server_id
        WHERE """
        + " AND ".join(conditions)
        + """
        ORDER BY e.event_time DESC
        LIMIT :limit
        """
    )

    rows = session.execute(sql, parameters).mappings().all()
    events = [
        ScoreChangeEvent(
            event_time=row["event_time"],
            server_id=row["server_id"],
            server_name=row["server_name"],
            axis=row["axis"],
            field=row["field"],
            old_value=row["old_value"],
            new_value=row["new_value"],
            delta=row["delta"],
        )
        for row in rows
    ]

    return ScoreChangesResponse(
        since_days=bounded_since_days,
        count=len(events),
        events=events,
    )


@router.get("/scores/changes", response_model=ScoreChangesResponse)
def score_changes(
    server_id: Optional[str] = Query(default=None),
    since_days: int = Query(default=30, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> ScoreChangesResponse:
    return get_score_changes(
        server_id=server_id,
        since_days=since_days,
        limit=limit,
        session=session,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE score_change_events (
                    id INTEGER PRIMARY KEY,
                    event_time DATETIME NOT NULL,
                    server_id TEXT NOT NULL,
                    name TEXT,
                    old_value REAL,
                    new_value REAL,
                    delta REAL,
                    axis TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name)
                VALUES (:server_id, :name)
                """
            ),
            [
                {"server_id": "server-a", "name": "Server A"},
                {"server_id": "server-b", "name": "Server B"},
            ],
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO score_change_events
                    (event_time, server_id, name, old_value, new_value, delta, axis)
                VALUES
                    (:event_time, :server_id, :name, :old_value, :new_value, :delta, :axis)
                """
            ),
            [
                {
                    "event_time": now - timedelta(hours=1),
                    "server_id": "server-a",
                    "name": "overall",
                    "old_value": 70.0,
                    "new_value": 80.0,
                    "delta": 10.0,
                    "axis": "trust",
                },
                {
                    "event_time": now - timedelta(hours=2),
                    "server_id": "server-b",
                    "name": "overall",
                    "old_value": 60.0,
                    "new_value": 65.0,
                    "delta": 5.0,
                    "axis": "risk",
                },
                {
                    "event_time": now - timedelta(hours=3),
                    "server_id": "server-a",
                    "name": "security",
                    "old_value": 50.0,
                    "new_value": 55.0,
                    "delta": 5.0,
                    "axis": "security",
                },
            ],
        )

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scores/changes")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["events"]) == 3

        filtered = client.get("/api/scores/changes", params={"server_id": "server-a"})
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["count"] == 2
        assert all(
            event["server_id"] == "server-a" for event in filtered_payload["events"]
        )

    print("PASS")