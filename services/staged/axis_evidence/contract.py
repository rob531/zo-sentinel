from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import and_

router = APIRouter(prefix="/api")

class AxisEvidence(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]

class ServerEvidence(BaseModel):
    server_id: str
    server_name: str
    axes: Dict[str, AxisEvidence]

@router.get("/axis/evidence", response_model=ServerEvidence)
async def get_axis_evidence(server_id: str, db: Session = Depends(get_session)):
    # Get server details
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    axis_scores = db.query(McpLlmAxisScore).filter(
        and_(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name != None,
            McpLlmAxisScore.p_top != None,
            McpLlmAxisScore.p_critical != None,
            McpLlmAxisScore.p_danger != None
        )
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis evidence found for server")

    # Build response
    axes = {}
    for score in axis_scores:
        axes[score.axis_name] = {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "escalated": score.escalated,
            "escalated_to": score.escalated_to
        }

    return {
        "server_id": server.server_id,
        "server_name": server.server_name,
        "axes": axes
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore
    from sqlalchemy.orm import sessionmaker

    # Create in-memory SQLite database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        db.add(McpServerRegistry(
            server_id="srv1",
            server_name="Test Server 1",
            meta={"test": "data"}
        ))

        db.add(McpLlmAxisScore(
            server_id="srv1",
            axis_name="axis1",
            label="Test Axis 1",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            escalated=False,
            escalated_to=None
        ))

        db.add(McpLlmAxisScore(
            server_id="srv1",
            axis_name="axis2",
            label="Test Axis 2",
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            escalated=True,
            escalated_to="admin"
        ))

        db.commit()

    # Create test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get("/api/axis/evidence?server_id=srv1")
    assert response.status_code == 200
    data = response.json()

    assert data["server_id"] == "srv1"
    assert data["server_name"] == "Test Server 1"
    assert len(data["axes"]) == 2

    axis1 = data["axes"]["axis1"]
    assert axis1["label"] == "Test Axis 1"
    assert axis1["p_top"] == 0.9
    assert axis1["p_critical"] == 0.8
    assert axis1["p_danger"] == 0.7
    assert axis1["escalated"] == False
    assert axis1["escalated_to"] == None

    axis2 = data["axes"]["axis2"]
    assert axis2["label"] == "Test Axis 2"
    assert axis2["p_top"] == 0.6
    assert axis2["p_critical"] == 0.5
    assert axis2["p_danger"] == 0.4
    assert axis2["escalated"] == True
    assert axis2["escalated_to"] == "admin"

    print("PASS")