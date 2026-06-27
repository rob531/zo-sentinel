import json
from typing import Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
import requests

# Define Pydantic models for request and response
class AxisScore(BaseModel):
    label: str
    p_top: float

class McpCurrentRiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    axis_scores: Dict[str, AxisScore]

class WriteServiceResponse(BaseModel):
    server_id: str
    axis_scores: Dict[str, Dict[str, float]] # Expecting {'axis_name': {'p_top': value}}

# Define risk tier rules
RISK_TIER_RULES = {
    "Critical": lambda scores: any(score["p_top"] >= 0.9 for score in scores.values()),
    "High": lambda scores: any(score["p_top"] >= 0.7 for score in scores.values()),
    "Medium": lambda scores: any(score["p_top"] >= 0.4 for score in scores.values()),
    "Low": lambda scores: True,  # Default if no other tier is met
}

# Mock write_service URL
WRITE_SERVICE_URL = "http://localhost:8001/query" # Assuming write_service runs on port 8001

# FastAPI app
app = FastAPI()

def get_risk_tier(axis_scores: Dict[str, Dict[str, float]]) -> str:
    """Computes the overall risk tier based on axis scores."""
    # Convert to the format expected by RISK_TIER_RULES
    formatted_scores = {
        label: {"p_top": data["p_top"]}
        for label, data in axis_scores.items()
    }

    for tier, rule in RISK_TIER_RULES.items():
        if rule(formatted_scores):
            return tier
    return "Low" # Should not be reached due to "Low" rule

@app.get("/mcp/{server_id}/current_risk_tier", response_model=McpCurrentRiskTierResponse)
async def get_mcp_current_risk_tier(server_id: str):
    """
    Reads mcp_llm_axis_scores for a given server_id, computes the overall risk tier,
    and returns the risk tier along with individual axis scores.
    """
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"query": "SELECT * FROM mcp_llm_axis_scores WHERE server_id = ?", "params": [server_id]},
            timeout=5 # Add a timeout for the request
        )
        response.raise_for_status()  # Raise an exception for bad status codes
        data: WriteServiceResponse = WriteServiceResponse(**response.json())

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Error communicating with write_service: {e}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid JSON response from write_service")
    except Exception as e: # Catch other potential Pydantic validation errors or unexpected issues
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

    if not data.axis_scores:
        raise HTTPException(status_code=404, detail=f"No axis scores found for server_id: {server_id}")

    risk_tier = get_risk_tier(data.axis_scores)

    # Format the axis scores for the response
    formatted_axis_scores = {
        label: AxisScore(label=label, p_top=score_data["p_top"])
        for label, score_data in data.axis_scores.items()
    }

    return McpCurrentRiskTierResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        axis_scores=formatted_axis_scores
    )

# --- Acceptance Test ---

# Mock the requests.post call for testing
class MockResponse:
    def __init__(self, json_data, status_code):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP Error: {self.status_code}")

def mock_post(*args, **kwargs):
    if kwargs.get("url") == WRITE_SERVICE_URL:
        query = kwargs.get("json", {}).get("query")
        params = kwargs.get("json", {}).get("params")

        if query == "SELECT * FROM mcp_llm_axis_scores WHERE server_id = ?" and params == ["test_server_123"]:
            mock_data = {
                "server_id": "test_server_123",
                "axis_scores": {
                    "security": {"p_top": 0.95},
                    "performance": {"p_top": 0.6},
                    "compliance": {"p_top": 0.3}
                }
            }
            return MockResponse(mock_data, 200)
        elif query == "SELECT * FROM mcp_llm_axis_scores WHERE server_id = ?" and params == ["test_server_high"]:
            mock_data = {
                "server_id": "test_server_high",
                "axis_scores": {
                    "security": {"p_top": 0.75},
                    "performance": {"p_top": 0.8},
                    "compliance": {"p_top": 0.5}
                }
            }
            return MockResponse(mock_data, 200)
        elif query == "SELECT * FROM mcp_llm_axis_scores WHERE server_id = ?" and params == ["test_server_medium"]:
            mock_data = {
                "server_id": "test_server_medium",
                "axis_scores": {
                    "security": {"p_top": 0.3},
                    "performance": {"p_top": 0.5},
                    "compliance": {"p_top": 0.2}
                }
            }
            return MockResponse(mock_data, 200)
        elif query == "SELECT * FROM mcp_llm_axis_scores WHERE server_id = ?" and params == ["test_server_low"]:
            mock_data = {
                "server_id": "test_server_low",
                "axis_scores": {
                    "security": {"p_top": 0.1},
                    "performance": {"p_top": 0.2},
                    "compliance": {"p_top": 0.05}
                }
            }
            return MockResponse(mock_data, 200)
        elif query == "SELECT * FROM mcp_llm_axis_scores WHERE server_id = ?" and params == ["test_server_not_found"]:
            return MockResponse({"detail": "Not Found"}, 404)
        else:
            return MockResponse({"detail": "Bad Request"}, 400)
    return MockResponse({"detail": "Not Found"}, 404)

@pytest.fixture(scope="module")
def test_client():
    # Patch requests.post for the duration of the tests
    original_post = requests.post
    requests.post = mock_post
    yield TestClient(app)
    # Restore the original requests.post after tests
    requests.post = original_post

def test_get_mcp_current_risk_tier(test_client: TestClient):
    # Test case 1: Critical risk tier
    response = test_client.get("/mcp/test_server_123/current_risk_tier")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test_server_123"
    assert data["risk_tier"] == "Critical"
    assert "security" in data["axis_scores"]
    assert data["axis_scores"]["security"]["label"] == "security"
    assert data["axis_scores"]["security"]["p_top"] == 0.95
    assert "performance" in data["axis_scores"]
    assert data["axis_scores"]["performance"]["label"] == "performance"
    assert data["axis_scores"]["performance"]["p_top"] == 0.6
    assert "compliance" in data["axis_scores"]
    assert data["axis_scores"]["compliance"]["label"] == "compliance"
    assert data["axis_scores"]["compliance"]["p_top"] == 0.3

    # Test case 2: High risk tier
    response = test_client.get("/mcp/test_server_high/current_risk_tier")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test_server_high"
    assert data["risk_tier"] == "High"
    assert "security" in data["axis_scores"]
    assert data["axis_scores"]["security"]["p_top"] == 0.75
    assert "performance" in data["axis_scores"]
    assert data["axis_scores"]["performance"]["p_top"] == 0.8
    assert "compliance" in data["axis_scores"]
    assert data["axis_scores"]["compliance"]["p_top"] == 0.5

    # Test case 3: Medium risk tier
    response = test_client.get("/mcp/test_server_medium/current_risk_tier")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test_server_medium"
    assert data["risk_tier"] == "Medium"
    assert "security" in data["axis_scores"]
    assert data["axis_scores"]["security"]["p_top"] == 0.3
    assert "performance" in data["axis_scores"]
    assert data["axis_scores"]["performance"]["p_top"] == 0.5
    assert "compliance" in data["axis_scores"]
    assert data["axis_scores"]["compliance"]["p_top"] == 0.2

    # Test case 4: Low risk tier
    response = test_client.get("/mcp/test_server_low/current_risk_tier")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test_server_low"
    assert data["risk_tier"] == "Low"
    assert "security" in data["axis_scores"]
    assert data["axis_scores"]["security"]["p_top"] == 0.1
    assert "performance" in data["axis_scores"]
    assert data["axis_scores"]["performance"]["p_top"] == 0.2
    assert "compliance" in data["axis_scores"]
    assert data["axis_scores"]["compliance"]["p_top"] == 0.05

    # Test case 5: Server not found
    response = test_client.get("/mcp/test_server_not_found/current_risk_tier")
    assert response.status_code == 404
    assert response.json() == {"detail": "No axis scores found for server_id: test_server_not_found"}

    print("PASS")

if __name__ == "__main__":
    # This block is for running the FastAPI app directly, not for tests.
    # For testing, use pytest.
    import uvicorn
    print("Running FastAPI application. Use pytest for testing.")
    # To run the app: uvicorn mcp_current_risk_tier_api:app --reload
    # To run tests: pytest mcp_current_risk_tier_api.py
    uvicorn.run(app, host="0.0.0.0", port=8000)