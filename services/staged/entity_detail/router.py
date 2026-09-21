from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, List

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

class SignalScore(BaseModel):
    label: str
    p_top: float

class EntityDetail(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    signals: Dict[str, SignalScore]

@router.get("/api/entities/{server_id}", response_model=EntityDetail)
async def get_entity_detail(server_id: str, session: Session = Depends(get_session)) -> EntityDetail:
    # Get server details
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

    return {
        "server_id": server.server_id,
        "name": server.name,
        "risk_tier": server.risk_tier,
        "verdict": server.verdict,
        "signals": signals
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    def get_test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    from app import dependency_overrides
    dependency_overrides[get_session] = get_test_session

    # Create test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    with TestSession() as session:
        # Add test server
        test_server = McpServerRegistry(
            server_id="test-server-1",
            name="Test Server 1",
            risk_tier="high",
            verdict="malicious"
        )
        session.add(test_server)

        # Add test axis scores
        test_scores = [
            McpLlmAxisScore(
                server_id="test-server-1",
                axis_name="axis1",
                label="label1",
                p_top=0.95
            ),
            McpLlmAxisScore(
                server_id="test-server-1",
                axis_name="axis2",
                label="label2",
                p_top=0.85
            )
        ]
        session.add_all(test_scores)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/entities/test-server-1")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": "test-server-1",
        "name": "Test Server 1",
        "risk_tier": "high",
        "verdict": "malicious",
        "signals": {
            "axis1": {"label": "label1", "p_top": 0.95},
            "axis2": {"label": "label2", "p_top": 0.85}
        }
    }

    print("PASS")