from __future__ import annotations

from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Dict, List

from fastapi import Depends, Query
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


async def get_risk_register(
    session: Any = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    effective_limit = min(max(int(limit), 1), 200)

    try:
        sync_session = getattr(session, "sync_session", session)
        bind = sync_session.get_bind()
        risk_columns = {
            column["name"]
            for column in sa_inspect(bind).get_columns("mcp_risk_register")
        }
    except Exception:
        risk_columns = set()

    if "risk_tier" in risk_columns:
        risk_expression = (
            "COALESCE(CAST(rr.risk_tier AS TEXT), r.risk_tier)"
        )
    elif "overall_risk" in risk_columns:
        risk_expression = (
            "COALESCE(r.risk_tier, CAST(rr.overall_risk AS TEXT))"
        )
    else:
        risk_expression = "r.risk_tier"

    registry_table = McpServerRegistry.__tablename__
    statement = text(
        f"""
        SELECT
            rr.server_id AS server_id,
            r.name AS name,
            {risk_expression} AS risk_tier,
            rr.computed_at AS computed_at
        FROM mcp_risk_register AS rr
        INNER JOIN {registry_table} AS r
            ON r.server_id = rr.server_id
        ORDER BY rr.computed_at DESC, rr.server_id ASC
        LIMIT :limit
        """
    )

    result = session.execute(statement, {"limit": effective_limit})
    if isawaitable(result):
        result = await result

    entries: List[Dict[str, Any]] = [
        dict(row) for row in result.mappings().all()
    ]

    return {
        "count": len(entries),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }


get_risk_register_ledger = get_risk_register


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite+pysqlite://",
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
                    name TEXT NOT NULL,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    server_id TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    overall_risk REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES
                    ('server-1', 'Server One', 'medium'),
                    ('server-2', 'Server Two', 'critical'),
                    ('server-3', 'Server Three', 'low')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register (server_id, computed_at, overall_risk)
                VALUES
                    ('server-1', '2024-01-01T00:00:00+00:00', 0.4),
                    ('server-2', '2024-03-01T00:00:00+00:00', 0.9),
                    ('server-3', '2024-02-01T00:00:00+00:00', 0.2)
                """
            )
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/risk/register")
    async def risk_register_endpoint(
        session: Any = Depends(get_session),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return await get_risk_register(session=session, limit=limit)

    with TestClient(that_app) as client:
        response = client.get("/api/risk/register")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["entries"]) == 3
        assert payload["entries"][0]["server_id"] == "server-2"
        assert all(
            "server_id" in entry and "risk_tier" in entry
            for entry in payload["entries"]
        )

    print("PASS")