import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Dict, Any

@pytest.fixture
def client():
    return TestClient(app)

def test_get_mcp_risk_tier_trend(client):
    # Override the database session for testing
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_org = Org(name="Test Org", created_at=datetime.now())
    test_user = User(email="test@example.com", created_at=datetime.now())
    test_server = MCPServerRegistry(
        org_id=test_org.id,
        server_name="Test Server",
        created_at=datetime.now(),
        last_updated=datetime.now()
    )

    # Add test data to the session
    session = SessionLocal()
    session.add(test_org)
    session.add(test_user)
    session.add(test_server)
    session.commit()

    # Add some test scores
    test_scores = [
        MCPLLMAxisScores(
            server_id=test_server.id,
            axis="axis1",
            score=0.8,
            created_at=datetime.now() - timedelta(days=5)
        ),
        MCPLLMAxisScores(
            server_id=test_server.id,
            axis="axis1",
            score=0.7,
            created_at=datetime.now() - timedelta(days=4)
        ),
        MCPLLMAxisScores(
            server_id=test_server.id,
            axis="axis1",
            score=0.6,
            created_at=datetime.now() - timedelta(days=3)
        ),
        MCPLLMAxisScores(
            server_id=test_server.id,
            axis="axis1",
            score=0.5,
            created_at=datetime.now() - timedelta(days=2)
        ),
        MCPLLMAxisScores(
            server_id=test_server.id,
            axis="axis1",
            score=0.4,
            created_at=datetime.now() - timedelta(days=1)
        )
    ]
    session.add_all(test_scores)
    session.commit()

    # Make the request
    response = client.get("/dashboard/mcp-risk-tier-trend")

    # Assert the response
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check that the data contains the expected trend
    for item in data:
        assert "date" in item
        assert "risk_tier" in item
        assert "count" in item

    # Clean up
    session.close()
    app.dependency_overrides.clear()

if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    test_get_mcp_risk_tier_trend(client)
    print("PASS")