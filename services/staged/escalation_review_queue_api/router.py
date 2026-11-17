from .logic import *  # noqa: F401,F403

from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


@router.get("/escalations")
def get_escalations(
    axis_name: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    scores = McpLlmAxisScore.__table__
    servers = McpServerRegistry.__table__

    statement = (
        select(
            scores.c.server_id,
            servers.c.name.label("server_name"),
            scores.c.axis_name,
            scores.c.label,
            scores.c.label_index,
            scores.c.p_top,
            scores.c.p_critical,
            scores.c.p_danger,
            scores.c.escalated_to,
            scores.c.decision_rule_version,
            scores.c.model_version,
            scores.c.scored_at,
            servers.c.registry_source,
            servers.c.risk_tier,
        )
        .select_from(scores.outerjoin(servers, servers.c.server_id == scores.c.server_id))
        .where(scores.c.escalated.is_(True))
        .order_by(scores.c.scored_at.desc())
    )

    if axis_name is not None:
        statement = statement.where(scores.c.axis_name == axis_name)

    statement = statement.limit(limit)

    items = []
    for row in session.execute(statement):
        items.append(
            {
                "server_id": row.server_id,
                "server_name": row.server_name,
                "axis_name": row.axis_name,
                "label": row.label,
                "p_top": row.p_top,
                "p_critical": row.p_critical,
                "p_danger": row.p_danger,
                "escalated_to": row.escalated_to,
                "risk_tier": row.risk_tier,
                "scored_at": row.scored_at,
                "decision_rule_version": row.decision_rule_version,
            }
        )

    by_axis = dict(Counter(item["axis_name"] for item in items))
    return {"count": len(items), "items": items, "by_axis": by_axis}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    self_app = FastAPI()
    self_app.include_router(router)

    test_engine = create_engine = None
    from sqlalchemy import create_engine

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
                    name TEXT,
                    registry_source TEXT,
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
                    label_index INTEGER,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    scored_at TEXT,
                    escalated BOOLEAN
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, risk_tier)
                VALUES
                    ('srv-known', 'Known Server', 'internal', 'high'),
                    ('srv-second', 'Second Server', 'internal', 'medium'),
                    ('srv-third', 'Third Server', 'external', 'low')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index,
                     p_top, p_critical, p_danger, escalated_to,
                     decision_rule_version, model_version, scored_at, escalated)
                VALUES
                    ('srv-known', 'overall_risk', 'critical', 3,
                     0.10, 0.70, 0.20, 'security_team',
                     'rules-v1', 'model-v1', '2025-01-04T00:00:00', 1),
                    ('srv-known', 'overall_risk', 'danger', 2,
                     0.20, 0.40, 0.40, 'security_team',
                     'rules-v1', 'model-v1', '2025-01-03T00:00:00', 1),
                    ('srv-second', 'auth_strength', 'warning', 1,
                     0.50, 0.20, 0.30, 'platform_owner',
                     'rules-v1', 'model-v1', '2025-01-02T00:00:00', 1),
                    ('srv-third', 'exploit_surface', 'info', 0,
                     0.80, 0.10, 0.10, 'soc',
                     'rules-v1', 'model-v1', '2025-01-01T00:00:00', 1)
                """
            )
        )

    def override_get_session():
        database_session = TestSession()
        try:
            yield database_session
        finally:
            database_session.close()

    self_app.dependency_overrides[get_session] = override_get_session

    with TestClient(self_app) as client:
        response = client.get("/api/scoring/escalations")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 4
        assert any(
            item["server_id"] == "srv-known" and item["escalated_to"] == "security_team"
            for item in payload["items"]
        )
        assert sum(payload["by_axis"].values()) == payload["count"]

    print("PASS")