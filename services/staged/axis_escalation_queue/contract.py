from __future__ import annotations

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


class EscalationItem(BaseModel):
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated_to: Optional[str]
    scored_at: Optional[datetime]
    model_version: str


class EscalationsResponse(BaseModel):
    count: int
    items: list[EscalationItem]


router = APIRouter(prefix="/api")


@router.get(
    "/axes/escalations",
    response_model=EscalationsResponse,
)
def get_axis_escalations(
    axis: Optional[str] = Query(default=None),
    tier: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    days: Optional[int] = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> EscalationsResponse:
    statement = (
        select(McpLlmAxisScore, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.escalated.is_(True))
    )

    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)
    if tier is not None:
        statement = statement.where(McpServerRegistry.risk_tier == tier)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        statement = statement.where(McpLlmAxisScore.scored_at >= cutoff)

    statement = statement.order_by(
        McpLlmAxisScore.scored_at.desc(),
        McpLlmAxisScore.id.desc(),
    ).limit(limit)

    rows = session.execute(statement).all()
    items = [
        EscalationItem(
            server_id=score.server_id,
            server_name=server.name,
            risk_tier=server.risk_tier,
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            escalated_to=score.escalated_to,
            scored_at=score.scored_at,
            model_version=score.model_version,
        )
        for score, server in rows
    ]
    return EscalationsResponse(count=len(items), items=items)


def run_self_test() -> bool:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(bind=engine, checkfirst=True)

    now = datetime.now(timezone.utc)
    session = TestingSession()
    try:
        servers = [
            McpServerRegistry(
                server_id="server-security",
                name="Security Server",
                risk_tier="high",
                verdict="review",
                trust_score=72.5,
            ),
            McpServerRegistry(
                server_id="server-quality",
                name="Quality Server",
                risk_tier="medium",
                verdict="conditional",
                trust_score=81.0,
            ),
            McpServerRegistry(
                server_id="server-reliability",
                name="Reliability Server",
                risk_tier="low",
                verdict="approved",
                trust_score=94.0,
            ),
        ]
        session.add_all(servers)
        session.flush()

        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-security",
                    axis_name="security",
                    label="critical",
                    label_index=3,
                    p_top=0.94,
                    p_critical=0.94,
                    p_danger=0.91,
                    escalated=True,
                    escalated_to="security-review",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-quality",
                    axis_name="quality",
                    label="high",
                    label_index=2,
                    p_top=0.88,
                    p_critical=0.12,
                    p_danger=0.45,
                    escalated=True,
                    escalated_to="quality-review",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-reliability",
                    axis_name="security",
                    label="medium",
                    label_index=1,
                    p_top=0.79,
                    p_critical=0.21,
                    p_danger=0.38,
                    escalated=True,
                    escalated_to="security-review",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    scored_at=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/axes/escalations")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 3, payload
        assert len(payload["items"]) == 3, payload
        assert all("axis_name" in item for item in payload["items"]), payload
        assert all("escalated_to" in item for item in payload["items"]), payload

        filtered = client.get("/api/axes/escalations", params={"axis": "security"})
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["count"] == 2, filtered_payload
        assert all(item["axis_name"] == "security" for item in filtered_payload["items"])

    print("PASS")
    return True


def _self_test() -> bool:
    return run_self_test()


def main() -> bool:
    return run_self_test()


if __name__ == "__main__":
    main()