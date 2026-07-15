from fastapi import APIRouter, Depends, Query
from typing import List, Dict
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session

router = APIRouter()

def get_high_value_servers(
    min_overall_risk: float = Query(80.0, description="Minimum overall risk score threshold"),
    db: Session = Depends(get_session)
) -> List[Dict]:
    # Query the database for servers with overall_risk >= min_overall_risk
    servers = db.query(
        MCPServerRegistry.server_id,
        MCPLLMAxisScores.score.label("overall_risk"),
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.label
    ).join(
        MCPLLMAxisScores,
        (MCPServerRegistry.server_id == MCPLLMAxisScores.server_id) &
        (MCPLLMAxisScores.axis_name == 'overall_risk')
    ).filter(
        MCPLLMAxisScores.score >= min_overall_risk
    ).all()

    # Convert the result to a list of dictionaries
    return [
        {
            "server_id": server.server_id,
            "overall_risk": server.overall_risk,
            "risk_tier": server.risk_tier,
            "label": server.label
        }
        for server in servers
    ]

@router.get("/servers/high-value", response_model=List[Dict])
async def high_value_servers(
    min_overall_risk: float = Query(80.0, description="Minimum overall risk score threshold"),
    db: Session = Depends(get_session)
):
    return get_high_value_servers(min_overall_risk, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create a temporary in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add some test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(
            server_id="server1",
            risk_tier="High",
            label="Test Server 1"
        ),
        MCPServerRegistry(
            server_id="server2",
            risk_tier="Medium",
            label="Test Server 2"
        ),
        MCPLLMAxisScores(
            server_id="server1",
            axis_name="overall_risk",
            score=90.0
        ),
        MCPLLMAxisScores(
            server_id="server2",
            axis_name="overall_risk",
            score=75.0
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/high-value?min_overall_risk=85.0")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "server1"
    assert response.json()[0]["overall_risk"] == 90.0

    print("PASS")