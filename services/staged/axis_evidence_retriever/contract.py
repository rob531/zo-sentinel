from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Optional, List
import datetime

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisEvidenceResponse(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float]
    escalated: bool
    escalated_to: Optional[str]
    model_version: str
    scored_at: str
    criteria_version: str


class AxisEvidenceRetrieverAPI(FastAPI):
    def __init__(self):
        super().__init__()

        @self.get("/api/servers/{server_id}/axes/{axis_name}/evidence")
        def get_axis_evidence(
            server_id: str,
            axis_name: str,
            session: Session = Depends(get_session)
        ) -> AxisEvidenceResponse:
            record = (
                session.query(McpLlmAxisScore)
                .filter(
                    McpLlmAxisScore.server_id == server_id,
                    McpLlmAxisScore.axis_name == axis_name
                )
                .first()
            )
            if not record:
                raise HTTPException(status_code=404, detail="Axis evidence not found")
            return AxisEvidenceResponse(
                server_id=record.server_id,
                axis_name=record.axis_name,
                label=record.label,
                label_index=record.label_index,
                p_top=record.p_top,
                p_critical=record.p_critical,
                p_danger=record.p_danger,
                probs=record.probs,
                escalated=record.escalated,
                escalated_to=record.escalated_to,
                model_version=record.model_version,
                scored_at=record.scored_at.isoformat(),
                criteria_version=record.decision_rule_version,
            )


app = AxisEvidenceRetrieverAPI()


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    McpLlmAxisScore.metadata.create_all(engine)

    session = SessionLocal()
    session.add_all([
        McpLlmAxisScore(
            server_id="srv-001",
            axis_name="safety",
            label="safe",
            label_index=0,
            p_top=0.85,
            p_critical=0.05,
            p_danger=0.10,
            probs=[0.85, 0.10, 0.05],
            escalated=False,
            escalated_to=None,
            model_version="v1.0",
            scored_at=datetime.datetime(2024, 1, 15, 10, 30, 0),
            decision_rule_version="rule-v2",
            adapter_sha256="abc123",
            id=1,
        ),
        McpLlmAxisScore(
            server_id="srv-002",
            axis_name="capability",
            label="high",
            label_index=2,
            p_top=0.70,
            p_critical=0.20,
            p_danger=0.10,
            probs=[0.10, 0.20, 0.70],
            escalated=True,
            escalated_to="human-review",
            model_version="v1.0",
            scored_at=datetime.datetime(2024, 1, 15, 11, 0, 0),
            decision_rule_version="rule-v2",
            adapter_sha256="def456",
            id=2,
        ),
    ])
    session.commit()

    def override_get_session():
        return session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    r1 = client.get("/api/servers/srv-001/axes/safety/evidence")
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["server_id"] == "srv-001"
    assert data1["axis_name"] == "safety"
    assert data1["label"] == "safe"
    assert data1["label_index"] == 0
    assert data1["p_top"] == 0.85
    assert data1["p_critical"] == 0.05
    assert data1["p_danger"] == 0.10
    assert data1["probs"] == [0.85, 0.10, 0.05]
    assert data1["escalated"] is False
    assert data1["escalated_to"] is None
    assert data1["model_version"] == "v1.0"
    assert data1["scored_at"] == "2024-01-15T10:30:00"
    assert data1["criteria_version"] == "rule-v2"

    r2 = client.get("/api/servers/srv-002/axes/capability/evidence")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["server_id"] == "srv-002"
    assert data2["axis_name"] == "capability"
    assert data2["label"] == "high"
    assert data2["label_index"] == 2
    assert data2["escalated"] is True
    assert data2["escalated_to"] == "human-review"
    assert data2["scored_at"] == "2024-01-15T11:00:00"

    print("PASS")