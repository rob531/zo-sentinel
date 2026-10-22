from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_escalations(
    session: Session,
    limit: int = 100,
    escalated_to: Optional[str] = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))

    params: dict[str, Any] = {"limit": limit}
    filter_sql = ""
    if escalated_to is not None:
        filter_sql = "AND a.escalated_to = :escalated_to"
        params["escalated_to"] = escalated_to

    count_sql = text(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT a.server_id, a.axis_name
            FROM mcp_llm_axis_scores AS a
            WHERE a.escalated = TRUE
            {filter_sql}
            GROUP BY a.server_id, a.axis_name
        ) AS unique_escalations
        """
    )

    targets_sql = text(
        f"""
        SELECT DISTINCT a.escalated_to
        FROM mcp_llm_axis_scores AS a
        WHERE a.escalated = TRUE
        {filter_sql}
          AND a.escalated_to IS NOT NULL
        ORDER BY a.escalated_to
        """
    )

    items_sql = text(
        f"""
        WITH ranked AS (
            SELECT
                a.server_id,
                r.name AS server_name,
                a.axis_name,
                a.label,
                a.p_top,
                a.p_critical,
                a.p_danger,
                a.escalated_to,
                a.scored_at,
                r.risk_tier,
                a.decision_rule_version,
                ROW_NUMBER() OVER (
                    PARTITION BY a.server_id, a.axis_name
                    ORDER BY a.scored_at DESC, a.id DESC
                ) AS row_number
            FROM mcp_llm_axis_scores AS a
            INNER JOIN mcp_server_registry AS r
                ON r.server_id = a.server_id
            WHERE a.escalated = TRUE
            {filter_sql}
        )
        SELECT
            server_id,
            server_name,
            axis_name,
            label,
            p_top,
            p_critical,
            p_danger,
            escalated_to,
            scored_at,
            risk_tier,
            decision_rule_version
        FROM ranked
        WHERE row_number = 1
        ORDER BY scored_at DESC, server_id, axis_name
        LIMIT :limit
        """
    )

    count = int(session.execute(count_sql, params).scalar_one())
    targets = [
        str(value)
        for value in session.execute(targets_sql, params).scalars().all()
        if value is not None
    ]
    items = [dict(row) for row in session.execute(items_sql, params).mappings().all()]

    return {
        "count": count,
        "escalated_to": targets,
        "items": items,
    }


def list_escalations(
    session: Session,
    limit: int = 100,
    escalated_to: Optional[str] = None,
) -> dict[str, Any]:
    return get_escalations(session, limit=limit, escalated_to=escalated_to)


def get_escalation_queue(
    session: Session,
    limit: int = 100,
    escalated_to: Optional[str] = None,
) -> dict[str, Any]:
    return get_escalations(session, limit=limit, escalated_to=escalated_to)


def fetch_escalations(
    session: Session,
    limit: int = 100,
    escalated_to: Optional[str] = None,
) -> dict[str, Any]:
    return get_escalations(session, limit=limit, escalated_to=escalated_to)


def get_axis_escalations(
    session: Session,
    limit: int = 100,
    escalated_to: Optional[str] = None,
) -> dict[str, Any]:
    return get_escalations(session, limit=limit, escalated_to=escalated_to)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
                    risk_tier TEXT,
                    verdict TEXT
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
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated BOOLEAN,
                    escalated_to TEXT,
                    scored_at TEXT,
                    decision_rule_version TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, verdict)
                VALUES
                    ('server-1', 'Primary Server', 'high', 'review'),
                    ('server-2', 'Secondary Server', 'low', 'approved')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, label, p_top, p_critical,
                     p_danger, escalated, escalated_to, scored_at,
                     decision_rule_version)
                VALUES
                    (1, 'server-1', 'security', 'critical', 0.91, 0.87, 0.72,
                     TRUE, 'security-team', '2025-01-02T00:00:00', 'v1'),
                    (2, 'server-1', 'quality', 'warning', 0.62, 0.20, 0.15,
                     FALSE, NULL, '2025-01-03T00:00:00', 'v1'),
                    (3, 'server-2', 'security', 'nominal', 0.20, 0.10, 0.05,
                     FALSE, NULL, '2025-01-01T00:00:00', 'v1')
                """
            )
        )

    def override_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/escalations")
    def escalations(
        session: Session = Depends(get_session),
        limit: int = 100,
        escalated_to: Optional[str] = None,
    ):
        return get_escalations(session, limit=limit, escalated_to=escalated_to)

    with TestClient(test_app) as client:
        response = client.get("/api/escalations")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["items"][0]["axis_name"] == "security"
        assert payload["items"][0]["escalated_to"] == "security-team"

        filtered = client.get(
            "/api/escalations",
            params={"escalated_to": "security-team"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["count"] == 1
        assert len(filtered.json()["items"]) == 1

        missing = client.get(
            "/api/escalations",
            params={"escalated_to": "does-not-exist"},
        )
        assert missing.status_code == 200
        assert missing.json()["count"] == 0
        assert missing.json()["items"] == []

    print("PASS")