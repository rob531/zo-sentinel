import pytest
from fastapi.testclient import TestClient
from app.db import get_session
from app.models import MCPServerRegistry, MCPSignalScores, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from app.main import app
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def client():
    app.dependency_overrides[get_session] = lambda: get_test_session()
    return TestClient(app)

def get_test_session():
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    return session

def test_summary_with_valid_date_range(client):
    response = client.get("/summary?start_date=2023-01-01&end_date=2023-01-31")
    assert response.status_code == 200
    assert "summary" in response.json()
    assert "risk_tiers" in response.json()["summary"]

def test_summary_with_invalid_date_range(client):
    response = client.get("/summary?start_date=2023-01-31&end_date=2023-01-01")
    assert response.status_code == 422
    assert "detail" in response.json()

def test_summary_with_missing_parameters(client):
    response = client.get("/summary")
    assert response.status_code == 422
    assert "detail" in response.json()

if __name__ == "__main__":
    import sys
    pytest.main([__file__, "-v"])
    print("PASS")