from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisEscalationServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    escalated_to: Optional[str] = None
    p_top: Optional[float] = None
    scored_at: Optional[datetime] = None


class AxisEscalationGroup(BaseModel):
    axis_name: str
    count: int
    servers: list[AxisEscalationServer]


class AxisEscalationResponse(BaseModel):
    total: int
    by_axis: list[AxisEscalationGroup]


router = APIRouter(prefix="/api")


def get_axis_escalations(
    session: Session,
    limit: int = 100,
    days: Optional[int] = None,
) -> AxisEscalationResponse:
    statement = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.scored_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.escalated.is_(True))
        .order_by(
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
        .limit(limit)
    )

    if days is not None:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        statement = statement.where(McpLlmAxisScore.scored_at >= cutoff)

    rows = session.execute(statement).all()
    grouped: OrderedDict[str, list[AxisEscalationServer]] = OrderedDict()

    for server_id, axis_name, escalated_to, p_top, scored_at, name, risk_tier in rows:
        server = AxisEscalationServer(
            server_id=server_id,
            name=name,
            risk_tier=risk_tier,
            escalated_to=escalated_to,
            p_top=p_top,
            scored_at=scored_at,
        )
        grouped.setdefault(axis_name, []).append(server)

    by_axis = [
        AxisEscalationGroup(
            axis_name=axis_name,
            count=len(servers),
            servers=servers,
        )
        for axis_name, servers in grouped.items()
    ]

    return AxisEscalationResponse(total=len(rows), by_axis=by_axis)


@router.get("/axes/escalations", response_model=AxisEscalationResponse)
def list_axis_escalations(
    limit: int = Query(default=100, ge=1, le=1000),
    days: Optional[int] = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> AxisEscalationResponse:
    return get_axis_escalations(session=session, limit=limit, days=days)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    McpLlmAxisScore.__table__.create(bind=engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with test_session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-alpha",
                    name="Alpha",
                    risk_tier="high",
                    registry_source="inventory",
                ),
                McpServerRegistry(
                    server_id="server-beta",
                    name="Beta",
                    risk_tier="critical",
                    registry_source="inventory",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-alpha",
                    axis_name="prompt_injection",
                    label="critical",
                    p_top=0.95,
                    p_critical=0.95,
                    p_danger=0.04,
                    escalated=True,
                    escalated_to="critical",
                    scored_at=now,
                    model_version="model-v1",
                    decision_rule_version="rules-v1",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-beta",
                    axis_name="data_exfiltration",
                    label="danger",
                    p_top=0.88,
                    p_critical=0.21,
                    p_danger=0.67,
                    escalated=True,
                    escalated_to="danger",
                    scored_at=now - timedelta(minutes=1),
                    model_version="model-v1",
                    decision_rule_version="rules-v1",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-alpha",
                    axis_name="unescalated_axis",
                    label="safe",
                    p_top=0.10,
                    p_critical=0.01,
                    p_danger=0.09,
                    escalated=False,
                    escalated_to=None,
                    scored_at=now - timedelta(minutes=2),
                    model_version="model-v1",
                    decision_rule_version="rules-v1",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/escalations")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 2, payload
        assert len(payload["by_axis"]) == 2, payload
        assert "unescalated_axis" not in {
            axis["axis_name"] for axis in payload["by_axis"]
        }, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()