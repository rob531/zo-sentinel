import pytest
from fastapi.testclient import TestClient
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from app.main import app
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

client = TestClient(app)

@pytest.fixture
def test_db():
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    yield SessionLocal()

    app.dependency_overrides.clear()

def test_get_axis_probabilities_valid_server_id(test_db):
    session = test_db()
    try:
        # Setup test data
        server = MCPServerRegistry(server_id="test_server", org_id=1, name="Test Server", description="Test Server")
        session.add(server)
        session.commit()

        axis_score = MCPLLMAxisScores(server_id="test_server", axis="test_axis", score=0.8)
        session.add(axis_score)
        session.commit()

        response = client.get("/axis-probabilities?server_id=test_server")
        assert response.status_code == 200
        assert response.json() == {
            "server_id": "test_server",
            "axis_probabilities": {
                "test_axis": 0.8
            }
        }
    finally:
        session.close()

def test_get_axis_probabilities_invalid_server_id(test_db):
    response = client.get("/axis-probabilities?server_id=invalid_server")
    assert response.status_code == 404
    assert response.json() == {"detail": "Server not found"}

def test_get_axis_probabilities_missing_server_id(test_db):
    response = client.get("/axis-probabilities")
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "loc": ["query", "server_id"],
                "msg": "field required",
                "type": "value_error.missing"
            }
        ]
    }

if __name__ == "__main__":
    import sys
    from pytest import main

    result = main([__file__, "-v"])
    sys.exit(result)