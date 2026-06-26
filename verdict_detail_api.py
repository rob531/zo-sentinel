# verdict_detail_api.py

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from fastapi.testclient import TestClient

# --- In-memory Data Stores (Simulating Database Tables) ---
# These dictionaries will act as our in-memory "database" for the self-test.
# In a real application, these would be replaced by actual database queries.

# Stores scores for the 6 risk axes for each server_id
# Key: server_id (int)
# Value: Dictionary of axis scores (e.g., {"axis_1_score": 0.1, ...})
mcp_llm_axis_scores_db: Dict[int, Dict[str, float]] = {}

# Stores overall risk information for each server_id
# Key: server_id (int)
# Value: Dictionary of overall risk score, verdict tier, and criteria version
# (e.g., {"overall_risk_score": 0.35, "verdict_tier": "Medium", "criteria_version": "v1.0"})
mcp_risk_register_db: Dict[int, Dict[str, Any]] = {}

# --- Pydantic Models for API Request/Response ---

class RiskAxesScores(BaseModel):
    """
    Represents the scores for the 6 individual risk axes.
    All fields are optional, as data might be missing for some axes.
    """
    axis_1_score: Optional[float] = Field(None, description="Score for risk axis 1")
    axis_2_score: Optional[float] = Field(None, description="Score for risk axis 2")
    axis_3_score: Optional[float] = Field(None, description="Score for risk axis 3")
    axis_4_score: Optional[float] = Field(None, description="Score for risk axis 4")
    axis_5_score: Optional[float] = Field(None, description="Score for risk axis 5")
    axis_6_score: Optional[float] = Field(None, description="Score for risk axis 6")

class VerdictDetail(BaseModel):
    """
    Represents the comprehensive verdict details for a given MCP server.
    Includes individual risk axis scores, overall risk score, verdict tier,
    and the criteria version used.
    """
    server_id: int = Field(..., description="Unique identifier for the MCP server")
    risk_axes: RiskAxesScores = Field(..., description="Detailed scores for the 6 risk axes")
    overall_risk_score: Optional[float] = Field(None, description="Overall aggregated risk score")
    verdict_tier: Optional[str] = Field(None, description="The verdict tier (e.g., 'Low', 'Medium', 'High')")
    criteria_version: Optional[str] = Field(None, description="Version of the criteria used for the verdict")

# --- FastAPI Router Definition ---

router = APIRouter()

@router.get(
    "/servers/{server_id}/verdict",
    response_model=VerdictDetail,
    summary="Get detailed verdict information for an MCP server",
    description="Retrieves comprehensive verdict details, including 6 risk axes scores, "
                "overall risk score, verdict tier, and criteria version for a specified MCP server ID. "
                "Handles cases where data might be partially or entirely missing by returning `None` for absent fields."
)
async def get_server_verdict(server_id: int):
    """
    Fetches detailed verdict information for a given MCP server ID.

    Args:
        server_id (int): The unique identifier of the MCP server.

    Returns:
        VerdictDetail: A structured response containing the verdict information.
                       Missing data fields will be represented as `None`.
    """
    # Retrieve data from the simulated axis scores table
    axis_scores_data = mcp_llm_axis_scores_db.get(server_id)

    # Retrieve data from the simulated risk register table
    risk_register_data = mcp_risk_register_db.get(server_id)

    # Construct RiskAxesScores object, filling with None if data is missing
    risk_axes = RiskAxesScores(
        axis_1_score=axis_scores_data.get("axis_1_score") if axis_scores_data else None,
        axis_2_score=axis_scores_data.get("axis_2_score") if axis_scores_data else None,
        axis_3_score=axis_scores_data.get("axis_3_score") if axis_scores_data else None,
        axis_4_score=axis_scores_data.get("axis_4_score") if axis_scores_data else None,
        axis_5_score=axis_scores_data.get("axis_5_score") if axis_scores_data else None,
        axis_6_score=axis_scores_data.get("axis_6_score") if axis_scores_data else None,
    )

    # Extract overall risk details, filling with None if data is missing
    overall_risk_score = risk_register_data.get("overall_risk_score") if risk_register_data else None
    verdict_tier = risk_register_data.get("verdict_tier") if risk_register_data else None
    criteria_version = risk_register_data.get("criteria_version") if risk_register_data else None

    # Return the complete VerdictDetail object
    return VerdictDetail(
        server_id=server_id,
        risk_axes=risk_axes,
        overall_risk_score=overall_risk_score,
        verdict_tier=verdict_tier,
        criteria_version=criteria_version,
    )

# --- Self-test Block ---
if __name__ == "__main__":
    app = FastAPI(
        title="MCP Server Verdict API",
        description="API to retrieve detailed verdict information for MCP servers.",
        version="1.0.0"
    )
    app.include_router(router)

    # Seed the in-memory databases for testing purposes
    # Case 1: Full data available for server_id 1
    mcp_llm_axis_scores_db[1] = {
        "axis_1_score": 0.1,
        "axis_2_score": 0.2,
        "axis_3_score": 0.3,
        "axis_4_score": 0.4,
        "axis_5_score": 0.5,
        "axis_6_score": 0.6,
    }
    mcp_risk_register_db[1] = {
        "overall_risk_score": 0.35,
        "verdict_tier": "Medium",
        "criteria_version": "v1.0",
    }

    # Case 2: Missing axis scores data for server_id 2
    mcp_risk_register_db[2] = {
        "overall_risk_score": 0.8,
        "verdict_tier": "High",
        "criteria_version": "v1.1",
    }

    # Case 3: Missing risk register data for server_id 3
    mcp_llm_axis_scores_db[3] = {
        "axis_1_score": 0.05,
        "axis_2_score": 0.1,
        "axis_3_score": 0.15,
        "axis_4_score": 0.2,
        "axis_5_score": 0.25,
        "axis_6_score": 0.3,
    }

    # Case 4: No data at all for server_id 99 (should return all Nones except server_id)

    client = TestClient(app)

    print("Running self-tests for verdict_detail_api.py...")

    # Test Case 1: Full data for server_id 1
    response = client.get("/servers/1/verdict")
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()
    assert data["server_id"] == 1
    assert data["risk_axes"] == {
        "axis_1_score": 0.1, "axis_2_score": 0.2, "axis_3_score": 0.3,
        "axis_4_score": 0.4, "axis_5_score": 0.5, "axis_6_score": 0.6,
    }
    assert data["overall_risk_score"] == 0.35
    assert data["verdict_tier"] == "Medium"
    assert data["criteria_version"] == "v1.0"
    print("✅ Test Case 1 (Full data) passed.")

    # Test Case 2: Missing axis scores for server_id 2
    response = client.get("/servers/2/verdict")
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()
    assert data["server_id"] == 2
    assert data["risk_axes"] == {
        "axis_1_score": None, "axis_2_score": None, "axis_3_score": None,
        "axis_4_score": None, "axis_5_score": None, "axis_6_score": None,
    }
    assert data["overall_risk_score"] == 0.8
    assert data["verdict_tier"] == "High"
    assert data["criteria_version"] == "v1.1"
    print("✅ Test Case 2 (Missing axis scores) passed.")

    # Test Case 3: Missing risk register data for server_id 3
    response = client.get("/servers/3/verdict")
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()
    assert data["server_id"] == 3
    assert data["risk_axes"] == {
        "axis_1_score": 0.05, "axis_2_score": 0.1, "axis_3_score": 0.15,
        "axis_4_score": 0.2, "axis_5_score": 0.25, "axis_6_score": 0.3,
    }
    assert data["overall_risk_score"] is None
    assert data["verdict_tier"] is None
    assert data["criteria_version"] is None
    print("✅ Test Case 3 (Missing risk register data) passed.")

    # Test Case 4: No data at all for server_id 99
    response = client.get("/servers/99/verdict")
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()
    assert data["server_id"] == 99
    assert data["risk_axes"] == {
        "axis_1_score": None, "axis_2_score": None, "axis_3_score": None,
        "axis_4_score": None, "axis_5_score": None, "axis_6_score": None,
    }
    assert data["overall_risk_score"] is None
    assert data["verdict_tier"] is None
    assert data["criteria_version"] is None
    print("✅ Test Case 4 (No data at all) passed.")

    print("\nAll self-tests completed successfully!")
    print("You can run this file directly to test the API logic.")
    print("To run the FastAPI application, save this file as verdict_detail_api.py and use:")
    print("  uvicorn verdict_detail_api:app --reload")