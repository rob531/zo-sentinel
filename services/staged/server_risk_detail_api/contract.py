from sqlalchemy.pool import StaticPool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: datetime

class ServerRiskDetail(BaseModel):
    server_id: int
    name: str
    verdict: str
    risk_tier: str
    trust_score: float
    last_assessed: datetime
    axes: List[AxisScore]

@router.get("/servers/{server_id}/risk-detail", response_model=ServerRiskDetail)
async def get_server_risk_detail(server_id: int, db: Session = Depends(get_session)):
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    return ServerRiskDetail(
        server_id=server.server_id,
        name=server.name,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        trust_score=server.trust_score,
        last_assessed=server.last_assessed,
        axes=[AxisScore(
            axis_name=axis.axis_name,
            label=axis.label,
            label_index=axis.label_index,
            p_top=axis.p_top,
            p_critical=axis.p_critical,
            p_danger=axis.p_danger,
            escalated=axis.escalated,
            scored_at=axis.scored_at
        ) for axis in axes]
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpLlmAxisScore
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", strategy="threadlocal")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    session = TestSession()
    session.add_all([
        McpServerRegistry(
            server_id=1,
            name="Test Server 1",
            verdict="clean",
            risk_tier="low",
            trust_score=0.95,
            last_assessed=datetime.now()
        ),
        McpServerRegistry(
            server_id=2,
            name="Test Server 2",
            verdict="suspicious",
            risk_tier="medium",
            trust_score=0.75,
            last_assessed=datetime.now()
        ),
        McpLlmAxisScore(
            server_id=1,
            axis_name="axis1",
            label="good",
            label_index=1,
            p_top=0.9,
            p_critical=0.1,
            p_danger=0.0,
            escalated=False,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id=1,
            axis_name="axis2",
            label="good",
            label_index=1,
            p_top=0.8,
            p_critical=0.2,
            p_danger=0.0,
            escalated=False,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id=1,
            axis_name="axis3",
            label="good",
            label_index=1,
            p_top=0.7,
            p_critical=0.3,
            p_danger=0.0,
            escalated=False,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="axis1",
            label="bad",
            label_index=0,
            p_top=0.1,
            p_critical=0.9,
            p_danger=0.0,
            escalated=True,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="axis2",
            label="bad",
            label_index=0,
            p_top=0.2,
            p_critical=0.8,
            p_danger=0.0,
            escalated=True,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="axis3",
            label="bad",
            label_index=0,
            p_top=0.3,
            p_critical=0.7,
            p_danger=0.0,
            escalated=True,
            scored_at=datetime.now()
        )
    ])
    session.commit()

    # Create test client
    client = TestClient(router)

    # Test endpoint
    response = client.get("/servers/1/risk-detail")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == 1
    assert len(data["axes"]) == 3

    print("PASS")