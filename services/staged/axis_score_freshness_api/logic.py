from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES: tuple[str, ...] = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _axis_rows(session: Session) -> dict[str, list[Any]]:
    statement = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.scored_at,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed.label("registry_last_scored"),
        )
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .order_by(
            McpLlmAxisScore.axis_name.asc(),
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    )
    rows: dict[str, list[Any]] = {axis: [] for axis in AXIS_NAMES}
    for row in session.execute(statement):
        if row.axis_name in rows and row.server_id is not None:
            rows[row.axis_name].append(row)
    return rows


def compute_axis_freshness(
    session: Session,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    reference_time = _utc_datetime(now or datetime.now(timezone.utc))
    rows_by_axis = _axis_rows(session)
    result: list[dict[str, Any]] = []

    for axis in AXIS_NAMES:
        latest_by_server: dict[str, Any] = {}
        for row in rows_by_axis[axis]:
            latest_by_server.setdefault(row.server_id, row)

        latest_rows = list(latest_by_server.values())
        if not latest_rows:
            result.append(
                {
                    "axis": axis,
                    "servers_scored": 0,
                    "newest_scored_at": None,
                    "oldest_scored_at": None,
                    "median_age_hours": None,
                    "stale_over_24h_count": 0,
                    "stale_over_72h_count": 0,
                }
            )
            continue

        scored_times = [_utc_datetime(row.scored_at) for row in latest_rows]
        ages = [
            max(0.0, (reference_time - scored_at).total_seconds() / 3600.0)
            for scored_at in scored_times
        ]

        newest_time = max(scored_times)
        oldest_time = min(scored_times)
        result.append(
            {
                "axis": axis,
                "servers_scored": len(latest_rows),
                "newest_scored_at": newest_time,
                "oldest_scored_at": oldest_time,
                "median_age_hours": round(float(median(ages)), 3),
                "stale_over_24h_count": sum(age > 24.0 for age in ages),
                "stale_over_72h_count": sum(age > 72.0 for age in ages),
            }
        )

    return result


def get_axis_freshness(
    session: Session,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return compute_axis_freshness(session, now=now)


def build_axis_freshness_payload(
    session: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _utc_datetime(now or datetime.now(timezone.utc))
    return {
        "generated_at": generated_at,
        "axes": compute_axis_freshness(session, now=generated_at),
    }


def axis_freshness_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return build_axis_freshness_payload(session)


calculate_axis_freshness = compute_axis_freshness
fetch_axis_freshness = compute_axis_freshness


if __name__ == "__main__":
    from fastapi import FastAPI
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
                    risk_tier TEXT,
                    last_assessed TIMESTAMP
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
                    p_top REAL,
                    scored_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, risk_tier, last_assessed)
                VALUES
                    ('server-fresh', 'low', CURRENT_TIMESTAMP),
                    ('server-old', 'medium', CURRENT_TIMESTAMP),
                    ('server-third', 'high', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, p_top, scored_at)
                VALUES
                    (1, 'server-fresh', 'overall_risk', 0.1, CURRENT_TIMESTAMP),
                    (2, 'server-old', 'auth_strength', 0.2,
                     datetime('now', '-48 hours')),
                    (3, 'server-third', 'capability_breadth', 0.3,
                     CURRENT_TIMESTAMP)
                """
            )
        )

    test_app = FastAPI()

    @test_app.get("/api/axis/freshness")
    def test_axis_freshness(session: Session = Depends(get_session)):
        return axis_freshness_endpoint(session)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/freshness")
        assert response.status_code == 200
        payload = response.json()
        axes = payload["axes"]
        assert {row["axis"] for row in axes} == set(AXIS_NAMES)
        assert sum(row["stale_over_24h_count"] for row in axes) == 1
        assert sum(row["stale_over_72h_count"] for row in axes) == 0

    print("PASS")