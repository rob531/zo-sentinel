from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session

router = APIRouter()

class SignalDetail(BaseModel):
    label: str
    p_top: float

class EntityDetailResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str]
    verdict: Optional[str]
    signals: Dict[str, SignalDetail]

def get_entity_detail(server_id: str, session: Session = Depends(get_session)) -> EntityDetailResponse:
    # Get server registry entry
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    axis_scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    # Prepare signals
    signals = {}
    for score in axis_scores:
        signals[score.axis_name] = {
            "label": score.label,
            "p_top": score.p_top
        }

    # Determine risk tier and verdict
    risk_tier = server.risk_tier
    verdict = server.verdict

    return EntityDetailResponse(
        server_id=server.server_id,
        name=server.name,
        risk_tier=risk_tier,
        verdict=verdict,
        signals=signals
    )

router.get("/api/entities/{server_id}", response_model=EntityDetailResponse)(get_entity_detail)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpLlmAxisScore, McpServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Create test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test client
    client = TestClient(router)

    # Seed test data
    test_server_id = "test-server-123"
    test_server = McpServerRegistry(
        server_id=test_server_id,
        name="Test Server",
        risk_tier="High",
        verdict="Malicious"
    )
    test_session = TestSession()
    test_session.add(test_server)

    test_axis1 = McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="axis1",
        label="Test Label 1",
        p_top=0.95
    )
    test_axis2 = McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="axis2",
        label="Test Label 2",
        p_top=0.85
    )
    test_session.add_all([test_axis1, test_axis2])
    test_session.commit()

    # Test endpoint
    response = client.get(f"/api/entities/{test_server_id}")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": test_server_id,
        "name": "Test Server",
        "risk_tier": "High",
        "verdict": "Malicious",
        "signals": {
            "axis1": {"label": "Test Label 1", "p_top": 0.95},
            "axis2": {"label": "Test Label 2", "p_top": 0.85}
        }
    }

    print("PASS")