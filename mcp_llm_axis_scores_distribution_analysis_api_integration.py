from fastapi.testclient import TestClient
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpScoreDisputes, Orgs, Users
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from mcp_llm_axis_scores_distribution_analysis_api import app

# Create a throwaway SQLite session for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_session():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_session] = override_get_session

client = TestClient(app)

@pytest.fixture(scope="module")
def setup_database():
    # Create the tables
    McpServerRegistry.__table__.create(bind=engine)
    McpLlmAxisScores.__table__.create(bind=engine)
    McpScoreDisputes.__table__.create(bind=engine)
    Orgs.__table__.create(bind=engine)
    Users.__table__.create(bind=engine)

    # Insert test data
    db = TestingSessionLocal()
    try:
        # Insert test data into McpServerRegistry
        db.add(McpServerRegistry(confidence=0.9, description="Test server 1"))
        db.add(McpServerRegistry(confidence=0.8, description="Test server 2"))
        db.commit()

        # Insert test data into McpLlmAxisScores
        db.add(McpLlmAxisScores(server_id=1, axis="test_axis_1", score=0.9))
        db.add(McpLlmAxisScores(server_id=1, axis="test_axis_2", score=0.8))
        db.add(McpLlmAxisScores(server_id=2, axis="test_axis_1", score=0.7))
        db.add(McpLlmAxisScores(server_id=2, axis="test_axis_2", score=0.6))
        db.commit()

        # Insert test data into McpScoreDisputes
        db.add(McpScoreDisputes(server_id=1, axis="test_axis_1", dispute_reason="Test dispute 1"))
        db.add(McpScoreDisputes(server_id=2, axis="test_axis_2", dispute_reason="Test dispute 2"))
        db.commit()

        # Insert test data into Orgs
        db.add(Orgs(name="Test Org 1", description="Test org description 1"))
        db.add(Orgs(name="Test Org 2", description="Test org description 2"))
        db.commit()

        # Insert test data into Users
        db.add(Users(org_id=1, name="Test User 1", email="test1@example.com"))
        db.add(Users(org_id=2, name="Test User 2", email="test2@example.com"))
        db.commit()
    finally:
        db.close()

def test_get_distribution_valid_parameters(setup_database):
    response = client.get("/distribution", params={"axis": "test_axis_1"})
    assert response.status_code == 200
    assert "distribution" in response.json()
    assert "disputes" in response.json()

def test_get_distribution_invalid_parameters(setup_database):
    response = client.get("/distribution", params={"axis": "invalid_axis"})
    assert response.status_code == 400
    assert "detail" in response.json()

def test_get_distribution_missing_parameters(setup_database):
    response = client.get("/distribution")
    assert response.status_code == 422
    assert "detail" in response.json()

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main(["-v", __file__]))