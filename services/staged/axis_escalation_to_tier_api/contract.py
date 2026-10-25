from __future__ import annotations

from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(tags=["scoring"])


@router.get("/scoring/escalation-to-tier")
def get_escalation_to_tier(
    axis_name: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_top,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .order_by(
            McpLlmAxisScore.axis_name.asc(),
            McpLlmAxisScore.escalated_to.asc(),
            McpLlmAxisScore.server_id.asc(),
        )
    )
    if axis_name is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis_name)

    records = session.execute(statement).all()
    grouped: dict[tuple[Any, Any], list[Any]] = {}
    for record in records:
        key = (record.axis_name, record.escalated_to)
        grouped.setdefault(key, []).append(record)

    rows: list[dict[str, Any]] = []
    for (current_axis, escalated_to), group in grouped.items():
        server_ids = {record.server_id for record in group if record.server_id is not None}
        matching_servers = {
            record.server_id
            for record in group
            if record.server_id is not None
            and escalated_to is not None
            and record.risk_tier == escalated_to
        }
        p_top_values = [
            float(record.p_top)
            for record in group
            if record.p_top is not None
        ]
        rows.append(
            {
                "axis_name": current_axis,
                "escalated_to": escalated_to,
                "tier_match_servers": len(matching_servers),
                "distinct_servers": len(server_ids),
                "median_p_top": float(median(p_top_values)) if p_top_values else None,
            }
        )

    if limit is not None:
        rows = rows[:limit]

    return {"total_axes": len(rows), "rows": rows}


def build_app() -> FastAPI:
    application = FastAPI(title="axis escalation to tier API")
    application.include_router(router, prefix="/api")
    return application


app = build_app()


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
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
                    label TEXT,
                    escalated INTEGER,
                    escalated_to TEXT,
                    p_top REAL,
                    scored_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES
                    ('server-1', 'Server One', 'high'),
                    ('server-2', 'Server Two', 'low'),
                    ('server-3', 'Server Three', 'medium')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, escalated, escalated_to, p_top, scored_at)
                VALUES
                    ('server-1', 'security', 'critical', 1, 'high', 0.9, '2024-01-01'),
                    ('server-2', 'security', 'warning', 0, 'high', 0.7, '2024-01-01'),
                    ('server-3', 'availability', 'warning', 1, 'medium', 0.5, '2024-01-01')
                """
            )
        )

    local_app = build_app()
    session = TestingSession()
    local_app.dependency_overrides[get_session] = lambda: session

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/scoring/escalation-to-tier")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert "total_axes" in payload
            assert "rows" in payload
            assert len(payload["rows"]) >= 1

            security_row = next(
                row for row in payload["rows"] if row["axis_name"] == "security"
            )
            assert security_row["distinct_servers"] == 2
            assert security_row["tier_match_servers"] == 1
            assert 0 <= security_row["median_p_top"] <= 1
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()