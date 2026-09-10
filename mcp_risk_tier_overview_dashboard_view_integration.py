import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores, MCPScoreDisputes, Orgs, Users
from app.main import app
# FU-369: removed an import of `override_get_session` from a module that does not
# exist in this tree. The name was never used in this file.

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def client(test_db):
    app.dependency_overrides[get_session] = lambda: test_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_data_fetching(client, test_db):
    org = Orgs(name="Test Org")
    user = Users(username="test_user", org=org)
    server = MCPServerRegistry(server_id="test_server", org=org)
    axis_scores = MCPAxisScores(server_id="test_server", axis="test_axis", score=0.5)
    test_db.add_all([org, user, server, axis_scores])
    test_db.commit()

    response = client.get("/mcp_risk_tier_overview")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["server_id"] == "test_server"
    assert data[0]["axis_scores"]["test_axis"] == 0.5

def test_rendering(client, test_db):
    org = Orgs(name="Test Org")
    user = Users(username="test_user", org=org)
    server = MCPServerRegistry(server_id="test_server", org=org)
    axis_scores = MCPAxisScores(server_id="test_server", axis="test_axis", score=0.5)
    test_db.add_all([org, user, server, axis_scores])
    test_db.commit()

    response = client.get("/mcp_risk_tier_overview")
    assert response.status_code == 200
    assert "test_server" in response.text
    assert "test_axis" in response.text
    assert "0.5" in response.text

def test_user_interactions(client, test_db):
    org = Orgs(name="Test Org")
    user = Users(username="test_user", org=org)
    server = MCPServerRegistry(server_id="test_server", org=org)
    axis_scores = MCPAxisScores(server_id="test_server", axis="test_axis", score=0.5)
    test_db.add_all([org, user, server, axis_scores])
    test_db.commit()

    response = client.post("/mcp_risk_tier_overview/dispute", json={"server_id": "test_server", "axis": "test_axis", "dispute_reason": "test_reason"})
    assert response.status_code == 200
    dispute = test_db.query(MCPScoreDisputes).filter_by(server_id="test_server", axis="test_axis").first()
    assert dispute is not None
    assert dispute.dispute_reason == "test_reason"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])