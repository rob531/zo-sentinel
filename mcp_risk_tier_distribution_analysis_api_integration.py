import pytest
from fastapi.testclient import TestClient

# Import the FastAPI application defined in the API module.
# The API module must expose a FastAPI instance named `app`.
from mcp_risk_tier_distribution_analysis_api import app  # type: ignore

client = TestClient(app)


def test_get_distribution_valid():
    """
    Send a request with valid query parameters and verify a successful response.
    The exact parameter names depend on the API implementation; adjust as needed.
    """
    response = client.get(
        "/distribution",
        params={
            "start_date": "2023-01-01",
            "end_date": "2023-01-31",
        },
    )
    assert response.status_code == 200
    json_body = response.json()
    # Expect a top‑level key that holds the distribution data.
    # The concrete key name may differ; the test checks for a mapping/list.
    assert isinstance(json_body, dict)
    # At least one of the common keys should be present.
    assert any(
        key in json_body for key in ("distribution", "data", "result")
    ), "Response JSON missing expected distribution key"


def test_get_distribution_invalid():
    """
    Provide an invalid date format to trigger FastAPI validation errors.
    """
    response = client.get(
        "/distribution",
        params={
            "start_date": "invalid-date",
            "end_date": "2023-01-31",
        },
    )
    # FastAPI returns 422 Unprocessable Entity for validation failures.
    assert response.status_code == 422
    json_body = response.json()
    assert "detail" in json_body


def test_get_distribution_missing():
    """
    Omit required query parameters entirely and expect a validation error.
    """
    response = client.get("/distribution")
    assert response.status_code == 422
    json_body = response.json()
    assert "detail" in json_body


if __name__ == "__main__":
    # Run the tests programmatically; exit code 0 indicates success.
    import sys
    import pytest

    exit_code = pytest.main([__file__, "-v"])
    if exit_code == 0:
        print("PASS")
    sys.exit(exit_code)