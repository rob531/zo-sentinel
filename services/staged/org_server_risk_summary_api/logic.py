from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from fastapi import Depends
from sqlalchemy import inspect, text

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, User

__all__ = [
    "get_risk_summary",
    "get_org_risk_summary",
    "get_org_risk_summary_data",
    "compute_risk_summary",
    "build_org_risk_summary",
]


def _table_columns(session: Any, table_name: str) -> Optional[set[str]]:
    try:
        bind = session.get_bind()
        inspector = inspect(bind)
        if not inspector.has_table(table_name):
            return None
        return {column["name"] for column in inspector.get_columns(table_name)}
    except Exception:
        return None


def _server_query(session: Any, org_id: int) -> str:
    registry_columns = _table_columns(session, "mcp_server_registry")
    user_columns = _table_columns(session, "users")

    has_registry_org = bool(
        registry_columns is not None and "org_id" in registry_columns
    )
    has_user_org = bool(user_columns is not None and "org_id" in user_columns)

    organization_filter = ""
    from_users = False

    if has_registry_org:
        organization_filter = """
            AND CAST(r.org_id AS TEXT) = CAST(:org_id AS TEXT)
        """
    elif has_user_org:
        organization_filter = """
            AND EXISTS (
                SELECT 1
                FROM users u
                WHERE CAST(u.org_id AS TEXT) = CAST(:org_id AS TEXT)
                  AND CAST(u.id AS TEXT) = CAST(r.server_id AS TEXT)
            )
        """
        from_users = True

    query = f"""
        SELECT
            r.server_id,
            r.name,
            r.verdict,
            r.risk_tier,
            r.trust_score,
            r.last_assessed,
            (
                SELECT a.p_top
                FROM mcp_llm_axis_scores a
                WHERE a.server_id = r.server_id
                  AND a.axis_name = 'overall_risk'
                ORDER BY
                    CASE WHEN a.scored_at IS NULL THEN 1 ELSE 0 END,
                    a.scored_at DESC,
                    a.id DESC
                LIMIT 1
            ) AS newest_overall_risk_p_top
        FROM mcp_server_registry r
        WHERE 1 = 1
        {organization_filter}
    """

    if from_users:
        query = query.replace(
            "FROM mcp_server_registry r\n        WHERE 1 = 1",
            "FROM mcp_server_registry r\n        WHERE 1 = 1",
        )

    return query


def get_risk_summary(org_id: int, session: Any = Depends(get_session)) -> Dict[str, Any]:
    result = session.execute(
        text(_server_query(session, org_id)),
        {"org_id": org_id},
    ).mappings()

    servers = list(result)

    by_verdict: Dict[str, int] = {}
    by_risk_tier: Dict[str, int] = {}
    trust_scores = []
    high_risk_count = 0

    for server in servers:
        verdict = server.get("verdict")
        risk_tier = server.get("risk_tier")
        trust_score = server.get("trust_score")
        p_top = server.get("newest_overall_risk_p_top")

        verdict_key = str(verdict) if verdict is not None else "UNKNOWN"
        risk_tier_key = str(risk_tier) if risk_tier is not None else "UNKNOWN"

        by_verdict[verdict_key] = by_verdict.get(verdict_key, 0) + 1
        by_risk_tier[risk_tier_key] = by_risk_tier.get(risk_tier_key, 0) + 1

        if trust_score is not None:
            trust_scores.append(float(trust_score))

        if p_top is not None and float(p_top) >= 0.8:
            high_risk_count += 1

    mean_trust_score = (
        sum(trust_scores) / len(trust_scores) if trust_scores else 0.0
    )

    return {
        "org_id": org_id,
        "total_servers": len(servers),
        "by_verdict": by_verdict,
        "by_risk_tier": by_risk_tier,
        "mean_trust_score": mean_trust_score,
        "high_risk_count": high_risk_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


get_org_risk_summary = get_risk_summary
get_org_risk_summary_data = get_risk_summary
compute_risk_summary = get_risk_summary
build_org_risk_summary = get_risk_summary


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
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
                CREATE TABLE users (
                    id VARCHAR PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    email VARCHAR NOT NULL,
                    password_hash VARCHAR NOT NULL,
                    role VARCHAR NOT NULL DEFAULT 'member',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    trust_score FLOAT,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    p_top FLOAT,
                    scored_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        server_rows = [
            ("srv-trusted", "Trusted Server", "TRUSTED_GENERAL", "LOW", 0.95),
            ("srv-caution", "Caution Server", "CAUTION_LIMITED", "MEDIUM", 0.70),
            ("srv-high", "High Risk Server", "HIGH_RISK_ISOLATED", "HIGH", 0.20),
        ]

        for server_id, name, verdict, risk_tier, trust_score in server_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, org_id, email, password_hash, role
                    ) VALUES (
                        :id, 1, :email, 'test-hash', 'member'
                    )
                    """
                ),
                {
                    "id": server_id,
                    "email": f"{server_id}@example.test",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id, name, verdict, risk_tier, trust_score,
                        last_assessed
                    ) VALUES (
                        :server_id, :name, :verdict, :risk_tier, :trust_score,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "server_id": server_id,
                    "name": name,
                    "verdict": verdict,
                    "risk_tier": risk_tier,
                    "trust_score": trust_score,
                },
            )

        for index, (server_id, _, _, _, _) in enumerate(server_rows, start=1):
            p_top = 0.9 if index == 3 else 0.4
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (
                        id, server_id, axis_name, p_top, scored_at
                    ) VALUES (
                        :id, :server_id, 'overall_risk', :p_top, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"id": index, "server_id": server_id, "p_top": p_top},
            )

    test_app = FastAPI()

    @test_app.get("/api/orgs/{org_id}/risk-summary")
    def test_risk_summary(org_id: int, session: Any = Depends(get_session)):
        return get_risk_summary(org_id, session)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/1/risk-summary")
    response.raise_for_status()
    payload = response.json()

    assert payload["total_servers"] == 3
    assert len(payload["by_verdict"]) == 3
    assert payload["high_risk_count"] == 1
    print("PASS")