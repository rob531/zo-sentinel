"""Data and computation layer for the axis-score escalation ledger."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class EscalationItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated_to: Optional[str] = None
    scored_at: Optional[datetime] = None
    decision_rule_version: Optional[str] = None


class EscalationResponse(BaseModel):
    days: int
    count: int
    items: List[EscalationItem]


def get_escalations(
    db: Session,
    days: int = 30,
    min_p: float = 0.5,
    axis: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return recent axis-score rows that require escalation triage."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=max(0, int(days)))

    statement = (
        select(
            McpLlmAxisScore.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.decision_rule_version,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            McpLlmAxisScore.scored_at >= cutoff,
            McpLlmAxisScore.scored_at <= now,
            or_(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.p_critical >= float(min_p),
                McpLlmAxisScore.p_danger >= float(min_p),
            ),
        )
    )

    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    statement = statement.order_by(
        desc(McpLlmAxisScore.p_critical).nulls_last(),
        desc(McpLlmAxisScore.scored_at),
    ).limit(max(0, int(limit)))

    return [dict(row) for row in db.execute(statement).mappings()]


def get_escalation_rows(
    db: Session,
    days: int = 30,
    min_p: float = 0.5,
    axis: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper for callers using the ledger-row name."""
    return get_escalations(
        db,
        days=days,
        min_p=min_p,
        axis=axis,
        limit=limit,
    )


def get_escalation_response(
    db: Session,
    days: int = 30,
    min_p: float = 0.5,
    axis: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Build the response envelope for the escalations endpoint."""
    items = get_escalations(
        db,
        days=days,
        min_p=min_p,
        axis=axis,
        limit=limit,
    )
    return {"days": int(days), "count": len(items), "items": items}


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSession()
    now = datetime.utcnow()
    db.add_all(
        [
            McpServerRegistry(
                server_id="server-critical",
                name="Critical MCP Server",
                risk_tier="critical",
                trust_score=0.2,
            ),
            McpServerRegistry(
                server_id="server-danger",
                name="Danger MCP Server",
                risk_tier="high",
                trust_score=0.5,
            ),
            McpServerRegistry(
                server_id="server-old",
                name="Old MCP Server",
                risk_tier="medium",
                trust_score=0.7,
            ),
            McpLlmAxisScore(
                id=1,
                server_id="server-critical",
                axis_name="tool_poisoning",
                model_version="test-v1",
                label="escalated",
                p_top=0.91,
                p_critical=0.9,
                p_danger=0.1,
                escalated=True,
                escalated_to="critical",
                scored_at=now - timedelta(days=1),
                decision_rule_version="rule-v1",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-danger",
                axis_name="tool_poisoning",
                model_version="test-v1",
                label="danger",
                p_top=0.7,
                p_critical=0.2,
                p_danger=0.6,
                escalated=False,
                scored_at=now - timedelta(days=2),
                decision_rule_version="rule-v1",
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-old",
                axis_name="tool_poisoning",
                model_version="test-v1",
                label="old",
                p_top=0.99,
                p_critical=0.95,
                p_danger=0.1,
                escalated=False,
                scored_at=now - timedelta(days=40),
                decision_rule_version="rule-v1",
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()

    @test_app.get("/api/scores/escalations", response_model=EscalationResponse)
    def escalations(
        days: int = 30,
        min_p: float = 0.5,
        axis: Optional[str] = None,
        limit: int = 100,
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_escalation_response(
            session,
            days=days,
            min_p=min_p,
            axis=axis,
            limit=limit,
        )

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/scores/escalations",
            params={"days": 30, "min_p": 0.5, "limit": 100},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert len(payload["items"]) == 2
        assert all(item["server_id"] != "server-old" for item in payload["items"])
        assert payload["items"][0]["server_id"] == "server-critical"
        assert payload["items"][0]["name"] == "Critical MCP Server"
        assert payload["items"][0]["risk_tier"] == "critical"

    db.close()
    engine.dispose()
    print("PASS")