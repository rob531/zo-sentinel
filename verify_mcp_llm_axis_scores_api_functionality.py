from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest

# Assume mcp_llm_axis_scores_api is a module containing the FastAPI app
# For testing purposes, we'll create a mock app here.
# In a real scenario, you would import your actual FastAPI app.

# --- Mock API Implementation (for testing purposes) ---
# This mock simulates the behavior of your mcp_llm_axis_scores_api.py
# In your actual project, you would import your FastAPI app from its module.

from fastapi import FastAPI, HTTPException

app = FastAPI()

# Mock data to simulate database responses
MOCK_DATA = {
    "server_123": {
        "risk_axes": {
            "data_privacy": {"p_top": 0.85},
            "security": {"p_top": 0.72},
            "compliance": {"p_top": 0.91},
            "ethical_considerations": {"p_top": 0.65},
            "bias_and_fairness": {"p_top": 0.78},
            "transparency_and_explainability": {"p_top": 0.88},
        },
        "overall_risk": 0.80,
        "risk_tier": "High",
        "criteria_version": "v1.2",
    },
    "server_456": {
        "risk_axes": {
            "data_privacy": {"p_top": 0.45},
            "security": {"p_top": 0.32},
            "compliance": {"p_top": 0.51},
            "ethical_considerations": {"p_top": 0.25},
            "bias_and_fairness": {"p_top": 0.38},
            "transparency_and_explainability": {"p_top": 0.48},
        },
        "overall_risk": 0.39,
        "risk_tier": "Low",
        "criteria_version": "v1.2",
    },
}

@app.get("/mcp_llm_axis_scores/{server_id}")
def get_mcp_llm_axis_scores(server_id: str):
    """
    Retrieves MCP LLM axis scores for a given server ID.
    """
    data = MOCK_DATA.get(server_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Server ID not found")
    return data

# --- End Mock API Implementation ---


client = TestClient(app)

def test_existing_server_id():
    """
    Tests the API endpoint with an existing server ID.
    """
    server_id = "server_123"
    response = client.get(f"/mcp_llm_axis_scores/{server_id}")

    assert response.status_code == 200
    data = response.json()

    # Assert that all required keys are present
    assert "risk_axes" in data
    assert "overall_risk" in data
    assert "risk_tier" in data
    assert "criteria_version" in data

    # Assert the structure and presence of risk axes
    risk_axes = data["risk_axes"]
    assert isinstance(risk_axes, dict)
    assert len(risk_axes) == 6  # Ensure all 6 axes are present

    expected_axes = [
        "data_privacy",
        "security",
        "compliance",
        "ethical_considerations",
        "bias_and_fairness",
        "transparency_and_explainability",
    ]
    for axis in expected_axes:
        assert axis in risk_axes
        assert "p_top" in risk_axes[axis]
        assert isinstance(risk_axes[axis]["p_top"], (int, float))
        assert 0.0 <= risk_axes[axis]["p_top"] <= 1.0

    # Assert overall risk and risk tier
    assert isinstance(data["overall_risk"], (int, float))
    assert 0.0 <= data["overall_risk"] <= 1.0
    assert isinstance(data["risk_tier"], str)
    assert data["risk_tier"] in ["Low", "Medium", "High", "Critical"] # Example tiers
    assert isinstance(data["criteria_version"], str)

def test_non_existent_server_id():
    """
    Tests the API endpoint with a non-existent server ID.
    """
    server_id = "non_existent_server"
    response = client.get(f"/mcp_llm_axis_scores/{server_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Server ID not found"}

def test_another_existing_server_id():
    """
    Tests the API endpoint with another existing server ID.
    """
    server_id = "server_456"
    response = client.get(f"/mcp_llm_axis_scores/{server_id}")

    assert response.status_code == 200
    data = response.json()

    assert "risk_axes" in data
    assert "overall_risk" in data
    assert "risk_tier" in data
    assert "criteria_version" in data

    risk_axes = data["risk_axes"]
    assert isinstance(risk_axes, dict)
    assert len(risk_axes) == 6

    expected_axes = [
        "data_privacy",
        "security",
        "compliance",
        "ethical_considerations",
        "bias_and_fairness",
        "transparency_and_explainability",
    ]
    for axis in expected_axes:
        assert axis in risk_axes
        assert "p_top" in risk_axes[axis]
        assert isinstance(risk_axes[axis]["p_top"], (int, float))
        assert 0.0 <= risk_axes[axis]["p_top"] <= 1.0

    assert isinstance(data["overall_risk"], (int, float))
    assert 0.0 <= data["overall_risk"] <= 1.0
    assert isinstance(data["risk_tier"], str)
    assert data["risk_tier"] in ["Low", "Medium", "High", "Critical"]
    assert isinstance(data["criteria_version"], str)


if __name__ == '__main__':
    # This block allows running the tests directly from the script.
    # In a real project, you would typically use pytest from the command line.
    print("Running tests for mcp_llm_axis_scores_api_functionality.py...")

    try:
        test_existing_server_id()
        print("Test 'test_existing_server_id' passed.")
        test_non_existent_server_id()
        print("Test 'test_non_existent_server_id' passed.")
        test_another_existing_server_id()
        print("Test 'test_another_existing_server_id' passed.")
        print("\nPASS")
    except AssertionError as e:
        print(f"\nFAIL: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")