from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisEvidence(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Any = None
    escalated: Optional[bool] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[datetime] = None


class ServerAxisEvidence(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    axes: list[AxisEvidence]


class AxisEvidenceResponse(BaseModel):
    servers: list[ServerAxisEvidence]


def _decode_probs(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def get_axis_evidence(session: Session, server_id: str | int) -> AxisEvidenceResponse:
    statement = (
        select(McpLlmAxisScore, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(McpServerRegistry.server_id == str(server_id))
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.model_version)
    )
    rows = session.execute(statement).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Server or axis evidence not found")

    _, server = rows[0]
    axes = [
        AxisEvidence(
            axis_name=score.axis_name,
            label=score.label,
            label_index=score.label_index,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            probs=_decode_probs(score.probs),
            escalated=score.escalated,
            decision_rule_version=score.decision_rule_version,
            model_version=score.model_version,
            scored_at=score.scored_at,
        )
        for score, _ in rows
    ]
    return AxisEvidenceResponse(
        servers=[
            ServerAxisEvidence(
                server_id=server.server_id,
                name=server.name,
                verdict=server.verdict,
                risk_tier=server.risk_tier,
                axes=axes,
            )
        ]
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from services.staged.axis_evidence_query.router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session():
        with TestSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    with TestSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="1",
                    name="ServerOne",
                    verdict="good",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="2",
                    name="ServerTwo",
                    verdict="bad",
                    risk_tier="high",
                ),
            ]
        )
        session.flush()

        axis_names = ["confidentiality", "integrity", "availability"]
        for server_number in (1, 2):
            for index, axis_name in enumerate(axis_names):
                session.add(
                    McpLlmAxisScore(
                        id=server_number * 10 + index,
                        server_id=str(server_number),
                        axis_name=axis_name,
                        label="low" if server_number == 1 else "high",
                        label_index=0 if server_number == 1 else 2,
                        p_top=0.7,
                        p_critical=0.2,
                        p_danger=0.1,
                        probs={"low": 0.7, "critical": 0.2, "danger": 0.1},
                        escalated=False,
                        decision_rule_version="v1",
                        model_version="m1",
                        scored_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        adapter_sha256="test-adapter-sha256",
                    )
                )
        session.commit()

    with TestClient(test_app) as client:
        for server_number in (1, 2):
            response = client.get(f"/api/servers/{server_number}/axis-evidence")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert "servers" in payload
            server_result = next(
                item
                for item in payload["servers"]
                if item["server_id"] == str(server_number)
            )
            axes = server_result["axes"]
            assert len(axes) > 0
            for axis in axes:
                assert 0.0 <= axis["p_top"] <= 1.0

    engine.dispose()
    print("PASS")