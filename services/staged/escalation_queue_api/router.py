from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api")

AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class EscalationQueueItem(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    verdict: str | None = None
    axis_name: str
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated_to: str | None = None
    decision_rule_version: str | None = None
    model_version: str | None = None
    scored_at: datetime | None = None


class EscalationQueueResponse(BaseModel):
    count: int
    items: list[EscalationQueueItem]


@router.get("/escalations", response_model=EscalationQueueResponse)
def get_escalations(
    axis: AxisName | None = Query(default=None),
    min_p_critical: float = Query(default=0.0),
    limit: int = Query(default=50, ge=1, le=500),
    session=Depends(get_session),
) -> EscalationQueueResponse:
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.scored_at,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(McpLlmAxisScore.escalated.is_(True))
        .where(McpLlmAxisScore.p_critical >= min_p_critical)
        .order_by(McpLlmAxisScore.p_critical.desc(), McpServerRegistry.server_id)
        .limit(limit)
    )

    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    rows = session.execute(statement).mappings().all()
    items = [dict(row) for row in rows]
    return EscalationQueueResponse(count=len(items), items=items)


if __name__ == "__main__":
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
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    label VARCHAR,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    scored_at DATETIME
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
                    ('server-1', 'Server One', 'high', 'blocked'),
                    ('server-2', 'Server Two', 'medium', 'review'),
                    ('server-3', 'Server Three', 'low', 'approved')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, label, p_top, p_critical,
                     p_danger, escalated, escalated_to, decision_rule_version,
                     model_version, scored_at)
                VALUES
                    (1, 'server-1', 'overall_risk', 'critical', 0.99, 0.95,
                     0.90, 1, 'overall_risk', 'v1', 'model-v1',
                     '2025-01-01T00:00:00'),
                    (2, 'server-2', 'auth_strength', 'warning', 0.70, 0.50,
                     0.40, 0, NULL, 'v1', 'model-v1',
                     '2025-01-01T00:00:00'),
                    (3, 'server-3', 'capability_breadth', 'normal', 0.30, 0.10,
                     0.05, 0, NULL, 'v1', 'model-v1',
                     '2025-01-01T00:00:00')
                """
            )
        )

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/escalations")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] >= 1
    assert payload["items"]
    assert payload["items"][0]["axis_name"]
    assert payload["items"][0]["escalated_to"]
    print("PASS")