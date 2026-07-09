import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_session
from app.models import User, MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def test_client():
    with TestClient(app) as client:
        yield client

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    testing_session = SessionLocal()
    app.dependency_overrides[get_session] = lambda: testing_session
    yield testing_session
    app.dependency_overrides.clear()

def test_axis_probabilities_analysis_dashboard_view(test_client, test_db):
    # Create test data
    user = User(email="test@example.com", created_at="2023-01-01")
    test_db.add(user)
    test_db.commit()

    server = MCPServerRegistry(
        name="Test Server",
        description="Test Description",
        owner_id=user.id,
        created_at="2023-01-01"
    )
    test_db.add(server)
    test_db.commit()

    axes = [
        "Axis1", "Axis2", "Axis3", "Axis4",
        "Axis5", "Axis6", "Axis7"
    ]

    for axis in axes:
        score = MCPLLMAxisScores(
            server_id=server.id,
            axis_name=axis,
            probability=0.5,
            created_at="2023-01-01"
        )
        test_db.add(score)
    test_db.commit()

    # Make the request
    response = test_client.get(f"/servers/{server.id}/axis-probabilities")

    # Verify the response
    assert response.status_code == 200
    for axis in axes:
        assert axis in response.text
        assert f"Probability: 0.5" in response.text

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
    print("PASS")