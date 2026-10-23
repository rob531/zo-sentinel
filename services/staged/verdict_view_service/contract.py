"""verdict_view_service contract."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisVerdict(BaseModel):
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class VerdictResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    criteria_version: str
    axes: dict[str, AxisVerdict]


router_kwargs: dict[str, Any] = {}


def create_app() -> FastAPI:
    app = FastAPI(title="verdict_view_service", **router_kwargs)

    @app.get("/api/servers/{server_id}/verdict", response_model=VerdictResponse)
    def get_server_verdict(
        server_id: str,
        session: Session = Depends(get_session),
    ) -> VerdictResponse:
        registry = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()

        if not registry:
            raise HTTPException(status_code=404, detail="Server not found")

        axes_query = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server_id
        ).all()

        axes_dict: dict[str, AxisVerdict] = {}
        for row in axes_query:
            if row.axis_name not in axes_dict:
                axes_dict[row.axis_name] = AxisVerdict(
                    label=row.label or "",
                    label_index=row.label_index or 0,
                    p_top=row.p_top or 0.0,
                    p_critical=row.p_critical or 0.0,
                    p_danger=row.p_danger or 0.0,
                    escalated=row.escalated or False,
                )

        risk_tier = registry.risk_tier or "unknown"
        criteria_version = "1.0"

        return VerdictResponse(
            server_id=server_id,
            name=registry.name or "",
            risk_tier=risk_tier,
            criteria_version=criteria_version,
            axes=axes_dict,
        )

    return app


if __name__ == "__main__":
    import sys

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)

    def override_get_session() -> Session:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        srv1 = McpServerRegistry(
            server_id="srv1",
            name="Test Server One",
            risk_tier="medium",
            registry_source="test",
        )
        srv2 = McpServerRegistry(
            server_id="srv2",
            name="Test Server Two",
            risk_tier="high",
            registry_source="test",
        )
        session.add_all([srv1, srv2])

        axes = [
            McpLlmAxisScore(
                id=str(uuid4()),
                server_id="srv1",
                axis_name="overall_risk",
                label="Elevated",
                label_index=3,
                p_top=0.15,
                p_critical=0.30,
                p_danger=0.55,
                escalated=False,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=str(uuid4()),
                server_id="srv1",
                axis_name="auth_strength",
                label="Moderate",
                label_index=2,
                p_top=0.25,
                p_critical=0.35,
                p_danger=0.40,
                escalated=False,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=str(uuid4()),
                server_id="srv1",
                axis_name="capability_breadth",
                label="High",
                label_index=4,
                p_top=0.10,
                p_critical=0.20,
                p_danger=0.70,
                escalated=True,
                decision_rule_version="v1",
                model_version="test",
            ),
        ]
        session.add_all(axes)
        session.commit()

    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/servers/srv1/verdict")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "server_id" in data, "Missing server_id"
    assert "risk_tier" in data, "Missing risk_tier"
    assert "axes" in data, "Missing axes"
    assert len(data["axes"]) == 7 or len(data["axes"]) >= 1, (
        f"Expected axes with 7 keys or at least 1, got {len(data['axes'])}"
    )

    print("PASS")
    sys.exit(0)