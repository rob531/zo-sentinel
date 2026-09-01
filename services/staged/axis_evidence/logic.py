from typing import Dict, List, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

class AxisEvidence(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]

class ServerAxisEvidence(BaseModel):
    server_id: str
    server_name: str
    axes: Dict[str, AxisEvidence]

def get_axis_evidence(server_id: str, session: Session = Depends(get_session)) -> ServerAxisEvidence:
    # Get server info
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores for the server
    axis_scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    # Build the response
    axes = {}
    for score in axis_scores:
        axes[score.axis_name] = AxisEvidence(
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            escalated=score.escalated,
            escalated_to=score.escalated_to
        )

    return ServerAxisEvidence(
        server_id=server.server_id,
        server_name=server.server_name,
        axes=axes
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Seed test data
    test_session = TestSessionLocal()
    test_server = McpServerRegistry(server_id="srv1", server_name="Test Server 1")
    test_session.add(test_server)
    test_session.commit()

    test_axis1 = McpLlmAxisScore(
        server_id="srv1",
        axis_name="axis1",
        label="Test Axis 1",
        p_top=0.9,
        p_critical=0.8,
        p_danger=0.7,
        escalated=True,
        escalated_to="admin"
    )
    test_axis2 = McpLlmAxisScore(
        server_id="srv1",
        axis_name="axis2",
        label="Test Axis 2",
        p_top=0.6,
        p_critical=0.5,
        p_danger=0.4,
        escalated=False,
        escalated_to=None
    )
    test_session.add_all([test_axis1, test_axis2])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/axis/evidence?server_id=srv1")
    assert response.status_code == 200

    data = response.json()
    assert data["server_id"] == "srv1"
    assert data["server_name"] == "Test Server 1"
    assert len(data["axes"]) == 2
    assert "axis1" in data["axes"]
    assert "axis2" in data["axes"]

    axis1 = data["axes"]["axis1"]
    assert axis1["label"] == "Test Axis 1"
    assert axis1["p_top"] == 0.9
    assert axis1["p_critical"] == 0.8
    assert axis1["p_danger"] == 0.7
    assert axis1["escalated"] == True
    assert axis1["escalated_to"] == "admin"

    axis2 = data["axes"]["axis2"]
    assert axis2["label"] == "Test Axis 2"
    assert axis2["p_top"] == 0.6
    assert axis2["p_critical"] == 0.5
    assert axis2["p_danger"] == 0.4
    assert axis2["escalated"] == False
    assert axis2["escalated_to"] is None

    print("PASS")