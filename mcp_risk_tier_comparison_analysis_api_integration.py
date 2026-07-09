from fastapi.testclient import TestClient
from app.db import get_session
from app.models import mcp_server_registry, mcp_llm_axis_scores, mcp_score_disputes, orgs, users
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

# Import the FastAPI app from the module to be tested
from mcp_risk_tier_comparison_analysis_api import app

# Create a test client
client = TestClient(app)

# Create a test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Override the get_session dependency
def override_get_session():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_session] = override_get_session

# Create the tables in the test database
def setup_module(module):
    mcp_server_registry.Base.metadata.create_all(bind=engine)
    mcp_llm_axis_scores.Base.metadata.create_all(bind=engine)
    mcp_score_disputes.Base.metadata.create_all(bind=engine)
    orgs.Base.metadata.create_all(bind=engine)
    users.Base.metadata.create_all(bind=engine)

# Drop the tables in the test database
def teardown_module(module):
    mcp_server_registry.Base.metadata.drop_all(bind=engine)
    mcp_llm_axis_scores.Base.metadata.drop_all(bind=engine)
    mcp_score_disputes.Base.metadata.drop_all(bind=engine)
    orgs.Base.metadata.drop_all(bind=engine)
    users.Base.metadata.drop_all(bind=engine)

# Test GET /comparison with valid parameters
def test_get_comparison_valid_parameters():
    response = client.get("/comparison?server_id=1&axis_id=1&start_date=2023-01-01&end_date=2023-01-31")
    assert response.status_code == 200
    assert response.json() == {"comparison": "valid"}

# Test GET /comparison with invalid parameters
def test_get_comparison_invalid_parameters():
    response = client.get("/comparison?server_id=invalid&axis_id=invalid&start_date=invalid&end_date=invalid")
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid parameters"}

# Test GET /comparison with missing parameters
def test_get_comparison_missing_parameters():
    response = client.get("/comparison")
    assert response.status_code == 422
    assert response.json() == {"detail": "Missing parameters"}

if __name__ == "__main__":
    pytest.main(["-v", __file__])