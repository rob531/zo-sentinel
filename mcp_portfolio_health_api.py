# mcp_portfolio_health_api.py

from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Dict, List, Any
import math
from fastapi.testclient import TestClient

# 1. Pydantic Models for Request/Response
class PortfolioHealthSummary(BaseModel):
    """
    Represents a summary of the overall health of the MCP portfolio.
    """
    total_active_mcps: int
    average_risk_score: float
    risk_tier_distribution: Dict[str, int]
    pending_submissions: int
    pending_exemptions: int

# Simulate database tables for in-memory testing
class InMemoryDB:
    """
    A simple in-memory database representation for testing purposes.
    In a real application, this would be replaced by actual database connections
    and ORM models.
    """
    def __init__(self, registry_data: List[Dict], scores_data: List[Dict], submissions_data: List[Dict]):
        self.mcp_server_registry = registry_data
        self.mcp_llm_axis_scores = scores_data
        self.mcp_submissions = submissions_data

# Dependency for database connection
# In a real application, this would yield a database session (e.g., SQLAlchemy session)
# For this exercise, it's a placeholder that will be overridden in tests.
def get_db_connection() -> InMemoryDB:
    """
    Placeholder for a database connection dependency.
    In a production environment, this would establish and yield a real DB connection.
    For testing, it will be overridden to provide an InMemoryDB instance.
    """
    # This should ideally raise an error or return a mock if not overridden
    # to prevent accidental use outside of a proper DB setup.
    raise NotImplementedError("Database connection not implemented for production environment. Use dependency override for testing.")

# 2. FastAPI Application Instance
app = FastAPI(
    title="MCP Portfolio Health API",
    description="Provides a summary of the overall health of the MCP portfolio, including metrics like active MCPs, risk scores, and pending items."
)

# Helper function to determine risk tier based on score
def get_risk_tier(score: int) -> str:
    """
    Categorizes a risk score into predefined tiers.
    Scores: 0-33 (Low), 34-66 (Medium), 67-100 (High).
    """
    if 0 <= score <= 33:
        return "Low"
    elif 34 <= score <= 66:
        return "Medium"
    elif 67 <= score <= 100:
        return "High"
    else:
        # This case should ideally not be reached with valid scores (0-100)
        return "Unknown"

# 3. FastAPI Endpoint
@app.get("/mcp_portfolio_health", response_model=PortfolioHealthSummary, status_code=status.HTTP_200_OK)
async def get_mcp_portfolio_health(db: InMemoryDB = Depends(get_db_connection)):
    """
    Retrieves a comprehensive health summary of the MCP portfolio.

    This endpoint aggregates data from `mcp_server_registry`, `mcp_llm_axis_scores`,
    and `mcp_submissions` to provide metrics such as:
    - Total number of active MCPs.
    - Average risk score across active MCPs.
    - Distribution of active MCPs across 'Low', 'Medium', and 'High' risk tiers.
    - Count of pending submissions.
    - Count of pending exemptions.
    """
    # 1. Query mcp_server_registry for active MCPs
    active_mcps = [mcp for mcp in db.mcp_server_registry if mcp["status"] == "active"]
    total_active_mcps = len(active_mcps)
    active_mcp_ids = {mcp["mcp_id"] for mcp in active_mcps}

    # 2. Query mcp_llm_axis_scores for risk data, considering only active MCPs
    active_mcp_scores = [
        score_rec["risk_score"]
        for score_rec in db.mcp_llm_axis_scores
        if score_rec["mcp_id"] in active_mcp_ids
    ]

    average_risk_score = sum(active_mcp_scores) / len(active_mcp_scores) if active_mcp_scores else 0.0

    risk_tier_distribution: Dict[str, int] = {"Low": 0, "Medium": 0, "High": 0}
    for score in active_mcp_scores:
        tier = get_risk_tier(score)
        # Ensure only valid tiers are incremented
        if tier in risk_tier_distribution:
            risk_tier_distribution[tier] += 1

    # 3. Query mcp_submissions for pending items
    pending_submissions = 0
    pending_exemptions = 0
    for submission in db.mcp_submissions:
        if submission["status"] == "pending":
            if submission["type"] == "submission":
                pending_submissions += 1
            elif submission["type"] == "exemption":
                pending_exemptions += 1

    # Return the aggregated health summary
    return PortfolioHealthSummary(
        total_active_mcps=total_active_mcps,
        average_risk_score=round(average_risk_score, 2),  # Round for consistent output
        risk_tier_distribution=risk_tier_distribution,
        pending_submissions=pending_submissions,
        pending_exemptions=pending_exemptions,
    )

# 4. __main__ block for Acceptance Testing
if __name__ == "__main__":
    # Seed an in-memory store with sample data
    seeded_registry = [
        {"mcp_id": "mcp1", "status": "active"},
        {"mcp_id": "mcp2", "status": "active"},
        {"mcp_id": "mcp3", "status": "inactive"}, # Inactive MCP
        {"mcp_id": "mcp4", "status": "active"},
        {"mcp_id": "mcp5", "status": "active"},
        {"mcp_id": "mcp6", "status": "inactive"}, # Inactive MCP
        {"mcp_id": "mcp7", "status": "active"},
    ]

    seeded_scores = [
        {"mcp_id": "mcp1", "risk_score": 20},  # Low risk
        {"mcp_id": "mcp2", "risk_score": 50},  # Medium risk
        {"mcp_id": "mcp3", "risk_score": 90},  # High risk (but mcp3 is inactive, so score won't be counted)
        {"mcp_id": "mcp4", "risk_score": 10},  # Low risk
        {"mcp_id": "mcp5", "risk_score": 70},  # High risk
        {"mcp_id": "mcp7", "risk_score": 40},  # Medium risk
        {"mcp_id": "mcp8", "risk_score": 30},  # Score for non-existent/inactive MCP, won't be counted
    ]

    seeded_submissions = [
        {"submission_id": "sub1", "mcp_id": "mcp1", "type": "submission", "status": "pending"},
        {"submission_id": "sub2", "mcp_id": "mcp2", "type": "exemption", "status": "pending"},
        {"submission_id": "sub3", "mcp_id": "mcp3", "type": "submission", "status": "approved"}, # Approved submission
        {"submission_id": "sub4", "mcp_id": "mcp4", "type": "submission", "status": "pending"},
        {"submission_id": "sub5", "mcp_id": "mcp5", "type": "exemption", "status": "approved"}, # Approved exemption
        {"submission_id": "sub6", "mcp_id": "mcp6", "type": "exemption", "status": "pending"}, # Pending for inactive MCP
    ]

    # Initialize the in-memory database with seeded data
    in_memory_store = InMemoryDB(seeded_registry, seeded_scores, seeded_submissions)

    # Define a dependency override function for testing
    def override_get_db_connection() -> InMemoryDB:
        return in_memory_store

    # Apply the dependency override for the test client
    app.dependency_overrides[get_db_connection] = override_get_db_connection

    # Create a TestClient instance
    client = TestClient(app)

    print("Running acceptance tests for /mcp_portfolio_health...")

    # Make a GET request to the endpoint
    response = client.get("/mcp_portfolio_health")

    # Assert the response status code
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    # Parse the JSON response
    data = response.json()

    # Calculate expected values based on seeded data:
    # Active MCPs: mcp1, mcp2, mcp4, mcp5, mcp7 (5 total)
    # Active MCP Scores:
    #   mcp1: 20 (Low)
    #   mcp2: 50 (Medium)
    #   mcp4: 10 (Low)
    #   mcp5: 70 (High)
    #   mcp7: 40 (Medium)
    # Sum of active scores: 20 + 50 + 10 + 70 + 40 = 190
    # Average risk score: 190 / 5 = 38.0
    # Risk Tier Distribution:
    #   Low: 2 (mcp1, mcp4)
    #   Medium: 2 (mcp2, mcp7)
    #   High: 1 (mcp5)
    # Pending Submissions:
    #   sub1 (status: pending, type: submission)
    #   sub4 (status: pending, type: submission)
    #   Total: 2
    # Pending Exemptions:
    #   sub2 (status: pending, type: exemption)
    #   sub6 (status: pending, type: exemption)
    #   Total: 2

    expected_data = {
        "total_active_mcps": 5,
        "average_risk_score": 38.0,
        "risk_tier_distribution": {"Low": 2, "Medium": 2, "High": 1},
        "pending_submissions": 2,
        "pending_exemptions": 2,
    }

    # Assert the returned data matches the expected data
    assert data == expected_data, f"Test failed: Expected {expected_data}, but got {data}"

    print("PASS: /mcp_portfolio_health returns a valid health summary.")