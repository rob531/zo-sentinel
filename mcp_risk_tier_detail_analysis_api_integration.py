import pytest
from fastapi.testclient import TestClient

# Import the FastAPI application from the target module.
# The module `mcp_risk_tier_detail_analysis_api` must expose a FastAPI instance named `app`.
from mcp_risk_tier_detail_analysis_api import app as fastapi_app

# Import the original DB session dependency so we can override it in tests.
from app.db import get_session

# ----------------------------------------------------------------------
# Helper: create a dummy SQLAlchemy session that does nothing.
# The endpoint may request a session via Depends(get_session); we replace it
# with a no‑op generator that yields `None`.  The endpoint should handle the
# absence of a real DB connection gracefully for the purpose of these tests.
# ----------------------------------------------------------------------
def dummy_session():
    """Yield a dummy session object (None) for dependency injection."""
    yield None

# Apply the dependency override globally for the TestClient.
fastapi_app.dependency_overrides[get_session] = dummy_session

client = TestClient(fastapi_app)


# ----------------------------------------------------------------------
# Test cases
# ----------------------------------------------------------------------
def test_detail_valid_parameters():
    """
    GET /detail with valid query parameters should return 200 and a JSON
    payload containing the echoed parameters.
    """
    params = {"org_id": "123", "risk_tier": "high"}
    response = client.get("/detail", params=params)
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    json_body = response.json()
    # Expected structure – adjust keys if the real API differs.
    assert isinstance(json_body, dict)
    assert json_body.get("org_id") == "123"
    assert json_body.get("risk_tier") == "high"
    # Additional sanity check – the endpoint should provide a 'detail' field.
    assert "detail" in json_body


def test_detail_invalid_parameters():
    """
    GET /detail with an invalid org_id (non‑numeric) should result in a
    validation error (422 Unprocessable Entity).
    """
    params = {"org_id": "invalid_id", "risk_tier": "high"}
    response = client.get("/detail", params=params)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"


def test_detail_missing_parameters():
    """
    GET /detail without required query parameters should result in a
    validation error (422 Unprocessable Entity).
    """
    response = client.get("/detail")  # No query parameters
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Run the tests programmatically.  pytest.main returns an exit code.
    exit_code = pytest.main([__file__, "-v"])
    if exit_code == 0:
        print("PASS")
    else:
        # Propagate the non‑zero exit code to indicate failure.
        raise SystemExit(exit_code)