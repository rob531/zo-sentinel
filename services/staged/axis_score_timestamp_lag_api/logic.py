from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


TOTAL_AXES = 7


class AxisScoreLagServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    latest_scored_at: Optional[datetime] = None
    lag_hours: Optional[float] = None
    axes_present: int
    axes_missing: int
    stalest_axis: Optional[str] = None


class AxisScoreLagResponse(BaseModel):
    hours: int
    generated_at: datetime
    servers: list[AxisScoreLagServer]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_axis_score_lag(
    session: Any,
    hours: int = 24,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if hours <= 0:
        raise ValueError("hours must be greater than zero")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=hours)

    score = McpLlmAxisScore
    registry = McpServerRegistry

    latest_scored_at = (
        _select(score.scored_at)
        .where(
            score.server_id == registry.server_id,
            score.scored_at >= cutoff,
        )
        .order_by(score.scored_at.desc(), score.id.desc())
        .limit(1)
        .scalar_subquery()
    )

    stalest_axis = (
        _select(score.axis_name)
        .where(
            score.server_id == registry.server_id,
            score.scored_at >= cutoff,
        )
        .order_by(score.scored_at.asc(), score.axis_name.asc(), score.id.asc())
        .limit(1)
        .scalar_subquery()
    )

    axes_present = (
        _select(_count_distinct(score.axis_name))
        .where(
            score.server_id == registry.server_id,
            score.scored_at >= cutoff,
        )
        .scalar_subquery()
    )

    statement = _select(
        registry.server_id,
        registry.name,
        registry.risk_tier,
        latest_scored_at.label("latest_scored_at"),
        stalest_axis.label("stalest_axis"),
        axes_present.label("axes_present"),
    ).select_from(registry)

    rows = session.execute(statement).mappings().all()
    servers: list[dict[str, Any]] = []

    for row in rows:
        latest = row["latest_scored_at"]
        if latest is not None:
            latest_utc = _as_utc(latest)
            lag_hours = max(
                0.0,
                (generated_at - latest_utc).total_seconds() / 3600.0,
            )
        else:
            latest_utc = None
            lag_hours = None

        present = min(int(row["axes_present"] or 0), TOTAL_AXES)
        servers.append(
            {
                "server_id": str(row["server_id"]),
                "name": row["name"],
                "risk_tier": row["risk_tier"],
                "latest_scored_at": latest_utc,
                "lag_hours": lag_hours,
                "axes_present": present,
                "axes_missing": max(0, TOTAL_AXES - present),
                "stalest_axis": row["stalest_axis"],
            }
        )

    servers.sort(
        key=lambda item: (
            item["lag_hours"] is None,
            -(item["lag_hours"] if item["lag_hours"] is not None else 0.0),
            item["server_id"],
        )
    )
    return servers[:limit]


def get_axis_score_lag_report(
    session: Any,
    hours: int = 24,
    limit: int = 100,
) -> dict[str, Any]:
    return {
        "hours": hours,
        "generated_at": datetime.now(timezone.utc),
        "servers": get_axis_score_lag(session, hours=hours, limit=limit),
    }


def get_axis_score_timestamp_lag(
    session: Any,
    hours: int = 24,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return get_axis_score_lag(session, hours=hours, limit=limit)


def _select(*columns: Any) -> Any:
    from sqlalchemy import select

    return select(*columns)


def _count_distinct(column: Any) -> Any:
    from sqlalchemy import distinct, func

    return func.count(distinct(column))


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    scored_at DATETIME,
                    model_version TEXT,
                    adapter_sha256 TEXT
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
                {
                    "server_id": "fresh-server",
                    "name": "Fresh Server",
                    "risk_tier": "low",
                },
                {
                    "server_id": "stale-server",
                    "name": "Stale Server",
                    "risk_tier": "high",
                },
            ],
        )

        now = datetime.now(timezone.utc)
        fresh_axes = [
            "axis_1",
            "axis_2",
            "axis_3",
            "axis_4",
            "axis_5",
            "axis_6",
            "axis_7",
        ]
        for index, axis_name in enumerate(fresh_axes):
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (
                        server_id, axis_name, scored_at, model_version, adapter_sha256
                    ) VALUES (
                        :server_id, :axis_name, :scored_at, :model_version, :adapter_sha256
                    )
                    """
                ),
                {
                    "server_id": "fresh-server",
                    "axis_name": axis_name,
                    "scored_at": now - timedelta(minutes=5 + index),
                    "model_version": "test-model",
                    "adapter_sha256": "test-adapter",
                },
            )
        for index, axis_name in enumerate(("axis_1", "axis_2")):
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (
                        server_id, axis_name, scored_at, model_version, adapter_sha256
                    ) VALUES (
                        :server_id, :axis_name, :scored_at, :model_version, :adapter_sha256
                    )
                    """
                ),
                {
                    "server_id": "stale-server",
                    "axis_name": axis_name,
                    "scored_at": now - timedelta(hours=48 + index),
                    "model_version": "test-model",
                    "adapter_sha256": "test-adapter",
                },
            )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get(
        "/api/axis/score-lag",
        response_model=AxisScoreLagResponse,
    )
    def axis_score_lag(
        hours: int = 168,
        limit: int = 100,
        session=Depends(get_session),
    ):
        return {
            "hours": hours,
            "generated_at": datetime.now(timezone.utc),
            "servers": get_axis_score_lag(session, hours=hours, limit=limit),
        }

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/score-lag?hours=168&limit=100")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["servers"]) == 2
        stale_server = payload["servers"][0]
        fresh_server = payload["servers"][1]
        assert stale_server["server_id"] == "stale-server"
        assert stale_server["lag_hours"] >= fresh_server["lag_hours"]
        assert stale_server["axes_missing"] == 5

    print("PASS")