from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session

router = APIRouter()

def get_axis_scores(server_id: str, db: Session = Depends(get_session)) -> Dict[str, Any]:
    # Verify server exists
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get all axis scores for the server
    scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    # Format the response
    result = {}
    for score in scores:
        result[score.axis] = {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "probs": score.probs
        }

    return result

router.get("/servers/{server_id}/axis-scores")(get_axis_scores)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create a test client
    client = TestClient(app)

    # Create a test server and axis scores
    test_server = MCPServerRegistry(server_id="test-server")
    test_scores = [
        MCPLLMAxisScores(
            server_id="test-server",
            axis="overall_risk",
            label="High",
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.4,
            probs=[0.1, 0.2, 0.3, 0.4]
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            axis="auth_strength",
            label="Medium",
            p_top=0.7,
            p_critical=0.5,
            p_danger=0.3,
            probs=[0.2, 0.3, 0.2, 0.3]
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            axis="capability_breadth",
            label="Low",
            p_top=0.6,
            p_critical=0.4,
            p_danger=0.2,
            probs=[0.3, 0.2, 0.3, 0.2]
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            axis="data_sensitivity",
            label="High",
            p_top=0.9,
            p_critical=0.7,
            p_danger=0.5,
            probs=[0.1, 0.2, 0.3, 0.4]
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            axis="network_egress",
            label="Medium",
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.4,
            probs=[0.2, 0.3, 0.2, 0.3]
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            axis="maintainer_trust",
            label="Low",
            p_top=0.7,
            p_critical=0.5,
            p_danger=0.3,
            probs=[0.3, 0.2, 0.3, 0.2]
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            axis="exploit_surface",
            label="High",
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.4,
            probs=[0.1, 0.2, 0.3, 0.4]
        )
    ]

    # Add test data to the session
    session = TestSession()
    session.add(test_server)
    session.add_all(test_scores)
    session.commit()

    # Test the endpoint
    response = client.get("/servers/test-server/axis-scores")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 7
    for axis in data:
        assert "label" in data[axis]
        assert "p_top" in data[axis]
        assert "p_critical" in data[axis]
        assert "p_danger" in data[axis]
        assert "probs" in data[axis]

    print("PASS")