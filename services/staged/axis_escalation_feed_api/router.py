from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalations

router = APIRouter(prefix="/api/axis", tags=["axis-escalations"])


class EscalationItem(BaseModel):
    server_id: str
    server_name: str | None = None
    risk_tier: str | None = None
    axis_name: str | None = None
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated_to: str | None = None
    decision_rule_version: str | None = None
    model_version: str | None = None
    scored_at: datetime | None = None


class EscalationSummary(BaseModel):
    by_axis: dict[str, int] = {}
    by_escalated_to: dict[str, int] = {}


class EscalationsResponse(BaseModel):
    escalations: list[EscalationItem] = []
    count: int = 0
    summary: EscalationSummary = EscalationSummary()


class EscalationSummaryResponse(BaseModel):
    by_axis: dict[str, int] = {}
    by_escalated_to: dict[str, int] = {}


EscalationResponse = EscalationsResponse


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("Escalation logic must return a mapping")


@router.get("/escalations", response_model=EscalationsResponse)
def list_axis_escalations(
    axis_name: str | None = Query(default=None),
    server_id: str | None = Query(default=None),
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _as_dict(
        get_escalations(
            session,
            axis_name=axis_name,
            server_id=server_id,
            days=days,
        )
    )


@router.get("/escalations/summary", response_model=EscalationSummaryResponse)
def get_axis_escalation_summary(
    axis_name: str | None = Query(default=None),
    server_id: str | None = Query(default=None),
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    payload = _as_dict(
        get_escalations(
            session,
            axis_name=axis_name,
            server_id=server_id,
            days=days,
        )
    )
    summary = payload.get("summary", payload)
    return {
        "by_axis": summary.get("by_axis", {}),
        "by_escalated_to": summary.get("by_escalated_to", {}),
    }


if __name__ == "__main__":
    from datetime import timedelta, timezone

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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    confidence FLOAT,
                    description TEXT,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta TEXT,
                    registry_source VARCHAR,
                    scan_count INTEGER,
                    trust_score FLOAT,
                    url VARCHAR,
                    verdict VARCHAR,
                    verdict_reasoning TEXT
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
                    label_index INTEGER,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    probs TEXT,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    scored_at DATETIME,
                    adapter_sha256 VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier)
                VALUES
                    ('server-1', 'Server One', 'high'),
                    ('server-2', 'Server Two', 'critical'),
                    ('server-3', 'Server Three', 'low')
                """
            )
        )

        scored_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, label, p_top, p_critical, p_danger,
                     escalated, escalated_to, decision_rule_version, model_version, scored_at)
                VALUES
                    (1, 'server-1', 'security', 'critical', 0.95, 0.91, 0.87,
                     1, 'high', 'rules-v1', 'model-v1', :scored_at),
                    (2, 'server-2', 'availability', 'critical', 0.93, 0.89, 0.84,
                     1, 'critical', 'rules-v1', 'model-v1', :scored_at),
                    (3, 'server-3', 'security', 'safe', 0.10, 0.03, 0.01,
                     0, NULL, 'rules-v1', 'model-v1', :scored_at),
                    (4, 'server-1', 'privacy', 'safe', 0.12, 0.04, 0.02,
                     0, NULL, 'rules-v1', 'model-v1', :scored_at)
                """
            ),
            {"scored_at": scored_at},
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/escalations")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert "security" in payload["summary"]["by_axis"]
        assert all(
            escalation["server_id"] != "server-3"
            for escalation in payload["escalations"]
        )

        summary_response = client.get("/api/axis/escalations/summary")
        assert summary_response.status_code == 200

    print("PASS")