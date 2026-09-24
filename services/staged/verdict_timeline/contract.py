from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Dict, List
from datetime import datetime

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

app = FastAPI(prefix="/api")


class AxisDetail(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class Verdict(BaseModel):
    scored_at: datetime
    risk_tier: str
    axes: Dict[str, AxisDetail]


class VerdictTimelineResponse(BaseModel):
    server_id: str
    server_name: str
    verdicts: List[Verdict]


@app.get("/verdicts/{server_id}/timeline", response_model=VerdictTimelineResponse)
def get_verdict_timeline(
    server_id: str,
    session: Session = Depends(get_session)
):
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).order_by(McpLlmAxisScore.scored_at).all()

    verdicts_map: Dict[datetime, dict] = {}
    for score in scores:
        scored_at = score.scored_at
        if scored_at not in verdicts_map:
            verdicts_map[scored_at] = Verdict(
                scored_at=scored_at,
                risk_tier=score.risk_tier,
                axes={}
            )
        verdicts_map[scored_at].axes[score.axis_name] = AxisDetail(
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            escalated=score.escalated
        )

    return VerdictTimelineResponse(
        server_id=server_id,
        server_name=server.name,
        verdicts=list(verdicts_map.values())
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    session = TestingSessionLocal()

    s1 = McpServerRegistry(server_id="srv-001", name="Server Alpha", url="https://alpha.example", risk_tier="medium")
    s2 = McpServerRegistry(server_id="srv-002", name="Server Beta", url="https://beta.example", risk_tier="low")
    session.add_all([s1, s2])
    session.commit()

    axes = ["security", "reliability", "performance", "compliance", "maintainability", "scalability", "availability"]

    for idx, d in enumerate([datetime(2024, 1, 15), datetime(2024, 2, 15), datetime(2024, 3, 15)]):
        for j, axis in enumerate(axes):
            session.add(McpLlmAxisScore(
                server_id="srv-001", axis_name=axis, label=f"{axis}_lbl",
                label_index=j, p_top=0.75 + j * 0.01, p_critical=0.10,
                p_danger=0.15, probs=[0.75, 0.15, 0.10], escalated=False,
                scored_at=d, model_version="v1.0", decision_rule_version="v1.0",
                adapter_sha256="sha256abc123"
            ))

    for idx, d in enumerate([datetime(2024, 1, 20), datetime(2024, 2, 20), datetime(2024, 3, 20)]):
        for j, axis in enumerate(axes):
            session.add(McpLlmAxisScore(
                server_id="srv-002", axis_name=axis, label=f"{axis}_lbl",
                label_index=j, p_top=0.85 + j * 0.01, p_critical=0.05,
                p_danger=0.10, probs=[0.85, 0.10, 0.05], escalated=False,
                scored_at=d, model_version="v1.0", decision_rule_version="v1.0",
                adapter_sha256="sha256def456"
            ))

    session.commit()
    session.close()

    resp = client.get("/api/verdicts/srv-001/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["verdicts"]) == 3
    assert len(data["verdicts"][0]["axes"]) == 7

    print("PASS")