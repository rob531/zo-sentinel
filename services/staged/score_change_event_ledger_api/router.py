from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_score_changes


router = APIRouter(prefix="/api")


class AxisChangeCount(BaseModel):
    axis_name: str
    count: int = Field(ge=0)
    up: int = Field(ge=0)
    down: int = Field(ge=0)


class RecentScoreChange(BaseModel):
    server_id: str
    server_name: str
    axis_name: str
    old_label: Optional[str] = None
    new_label: Optional[str] = None
    old_p_top: Optional[float] = None
    new_p_top: Optional[float] = None
    changed_at: str


class ScoreChangesResponse(BaseModel):
    total: int = Field(ge=0)
    by_axis: List[AxisChangeCount]
    recent: List[RecentScoreChange]


@router.get("/scores/changes", response_model=ScoreChangesResponse)
def score_changes(
    server_id: Optional[str] = Query(default=None),
    axis_name: Optional[str] = Query(default=None),
    hours: int = Query(default=168, ge=1),
    session: Session = Depends(get_session),
) -> ScoreChangesResponse:
    result = get_score_changes(
        session=session,
        server_id=server_id,
        axis_name=axis_name,
        hours=hours,
    )
    return ScoreChangesResponse.model_validate(result)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_change_events (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    old_p_top REAL,
                    new_p_top REAL,
                    old_label TEXT,
                    new_label TEXT,
                    changed_at TEXT NOT NULL,
                    source TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES (:server_id, :name, :risk_tier)
                """
            ),
            [
                {"server_id": "server-a", "name": "Server A", "risk_tier": "high"},
                {"server_id": "server-b", "name": "Server B", "risk_tier": "medium"},
            ],
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_change_events (
                    id, server_id, axis_name, old_p_top, new_p_top,
                    old_label, new_label, changed_at, source
                )
                VALUES (
                    :id, :server_id, :axis_name, :old_p_top, :new_p_top,
                    :old_label, :new_label, :changed_at, :source
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-a",
                    "axis_name": "security",
                    "old_p_top": 0.10,
                    "new_p_top": 0.80,
                    "old_label": "weak",
                    "new_label": "strong",
                    "changed_at": (now - timedelta(hours=1)).isoformat(),
                    "source": "contract",
                },
                {
                    "id": 2,
                    "server_id": "server-b",
                    "axis_name": "security",
                    "old_p_top": 0.80,
                    "new_p_top": 0.20,
                    "old_label": "strong",
                    "new_label": "weak",
                    "changed_at": (now - timedelta(hours=2)).isoformat(),
                    "source": "contract",
                },
                {
                    "id": 3,
                    "server_id": "server-a",
                    "axis_name": "quality",
                    "old_p_top": 0.30,
                    "new_p_top": 0.60,
                    "old_label": "fair",
                    "new_label": "good",
                    "changed_at": (now - timedelta(hours=3)).isoformat(),
                    "source": "contract",
                },
            ],
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scores/changes")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3
        assert {row["axis_name"] for row in payload["by_axis"]} == {
            "security",
            "quality",
        }
        axis_counts = {row["axis_name"]: row for row in payload["by_axis"]}
        assert axis_counts["security"]["count"] == 2
        assert axis_counts["security"]["up"] == 1
        assert axis_counts["security"]["down"] == 1
        assert axis_counts["quality"]["count"] == 1
        assert axis_counts["quality"]["up"] == 1
        assert axis_counts["quality"]["down"] == 0
        assert any(event["server_id"] == "server-a" for event in payload["recent"])
        assert any(
            event["axis_name"] == "security"
            and event["old_label"] == "weak"
            and event["new_label"] == "strong"
            for event in payload["recent"]
        )

    print("PASS")