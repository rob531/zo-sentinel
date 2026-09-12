from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import List, Optional
from datetime import datetime

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

app = FastAPI()


class AxisScoreItem(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Optional[List[float]] = None
    escalated: bool = False
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: datetime


class ServerAxisScoresResponse(BaseModel):
    server_id: str
    server_name: str
    risk_tier: Optional[str] = None
    axes: List[AxisScoreItem]


def get_server_axis_scores_logic(server_id: str, session: Session) -> dict:
    result = session.execute(
        text("""
            SELECT
                a.server_id,
                r.name as server_name,
                r.risk_tier,
                a.axis_name,
                a.label,
                a.label_index,
                a.p_top,
                a.p_critical,
                a.p_danger,
                a.probs,
                a.escalated,
                a.decision_rule_version,
                a.model_version,
                a.scored_at
            FROM mcp_llm_axis_scores a
            JOIN mcp_server_registry r ON a.server_id = r.server_id
            WHERE a.server_id = :server_id
            ORDER BY a.scored_at DESC
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    return {
        "server_id": rows[0].server_id,
        "server_name": rows[0].server_name,
        "risk_tier": rows[0].risk_tier,
        "axes": [
            AxisScoreItem(
                axis_name=row.axis_name,
                label=row.label,
                label_index=row.label_index,
                p_top=row.p_top,
                p_critical=row.p_critical,
                p_danger=row.p_danger,
                probs=row.probs,
                escalated=row.escalated,
                decision_rule_version=row.decision_rule_version,
                model_version=row.model_version,
                scored_at=row.scored_at
            )
            for row in rows
        ]
    }


@app.get("/api/servers/{server_id}/axis-scores", response_model=ServerAxisScoresResponse)
def get_server_axis_scores_endpoint(
    server_id: str,
    session: Session = Depends(get_session)
) -> dict:
    return get_server_axis_scores_logic(server_id, session)


if __name__ == "__main__":
    from app.models import Base
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    session = TestingSessionLocal()
    
    session.add(McpServerRegistry(server_id="srv_001", name="Test Server Alpha", risk_tier="high"))
    session.add(McpServerRegistry(server_id="srv_002", name="Test Server Beta", risk_tier="medium"))
    
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    axis_names = ["security", "reliability", "maintainability", "performance", "usability", "compatibility", "scalability"]
    
    for server_id in ["srv_001", "srv_002"]:
        for i in range(3):
            scored_at = base_time.replace(hour=12 + i)
            for axis in axis_names:
                session.add(McpLlmAxisScore(
                    server_id=server_id,
                    axis_name=axis,
                    label="good",
                    label_index=0,
                    p_top=0.8 - (i * 0.1),
                    p_critical=0.1,
                    p_danger=0.05,
                    probs=[0.8, 0.1, 0.05],
                    escalated=False,
                    decision_rule_version="v1",
                    model_version="v1",
                    scored_at=scored_at,
                    adapter_sha256="abc123"
                ))
    
    session.commit()
    session.close()
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response = client.get("/api/servers/srv_001/axis-scores")
    assert response.status_code == 200
    
    data = response.json()
    latest_score_axes = [a for a in data["axes"] if a["scored_at"] == data["axes"][0]["scored_at"]]
    assert len(latest_score_axes) >= 7, f"Expected >= 7 axes, got {len(latest_score_axes)}"
    
    p_top_08_found = any(a["p_top"] == 0.8 for a in data["axes"])
    assert p_top_08_found, "Expected p_top == 0.8 in axes"
    
    print("PASS")