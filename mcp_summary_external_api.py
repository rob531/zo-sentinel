import enum
from typing import List, Optional, Any, Generator

from fastapi import FastAPI, APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

# --- Pydantic Models and Enums ---

class RiskTierEnum(str, enum.Enum):
    """Enum for different risk tiers."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class MCPSummaryResponse(BaseModel):
    """Response model for a single MCP summary item."""
    mcp_name: str = Field(..., description="The name of the Model Context Protocol (MCP) server.")
    overall_risk: float = Field(..., description="The overall risk score for the MCP, derived from mcp_llm_axis_scores.")
    risk_tier: RiskTierEnum = Field(..., description="The assigned risk tier based on the overall_risk.")

# --- Risk Tier Assignment Logic ---

def assign_risk_tier(overall_risk: float) -> RiskTierEnum:
    """
    Assigns a risk tier based on the overall risk score.
    Thresholds are defined as:
    - LOW: overall_risk < 0.3
    - MEDIUM: 0.3 <= overall_risk < 0.6
    - HIGH: 0.6 <= overall_risk < 0.9
    - CRITICAL: 0.9 <= overall_risk
    """
    if overall_risk < 0.3:
        return RiskTierEnum.LOW
    elif overall_risk < 0.6:
        return RiskTierEnum.MEDIUM
    elif overall_risk < 0.9:
        return RiskTierEnum.HIGH
    else:
        return RiskTierEnum.CRITICAL

# --- Database Interaction Simulation (for API and Testing) ---

# This represents a simplified view of what a database record might look like,
# combining MCP name and its latest overall risk score.
# In a real application, this would likely involve a JOIN between an MCP table
# and an mcp_llm_axis_scores table to get the most recent score.
class MockMCPDBRecord(BaseModel):
    mcp_name: str
    overall_risk: float

# Placeholder for a database session dependency.
# In a real application, this would yield a SQLAlchemy session or similar.
# For testing, it will be overridden by the __main__ block.
def get_db_session() -> Generator[Any, None, None]:
    """
    Dependency that provides a database session.
    This function is a placeholder and should be overridden for actual use
    or testing with a concrete database implementation.
    """
    # This line should ideally not be reached in a properly configured app
    # or during tests where it's overridden.
    raise NotImplementedError("Database session dependency not implemented for production.")

# --- FastAPI Router ---

router = APIRouter()

@router.get(
    "/mcp_summary",
    response_model=List[MCPSummaryResponse],
    summary="Get a high-level summary of all Model Context Protocol (MCP) servers",
    description="""
    Retrieves a high-level summary of all Model Context Protocol (MCP) servers.
    Each summary includes the MCP's name, its current overall risk score (derived
    from `mcp_llm_axis_scores`), and an assigned risk tier (LOW, MEDIUM, HIGH, CRITICAL).
    The endpoint supports optional filtering by `risk_tier` and pagination
    using `skip` and `limit` parameters.
    """
)
async def get_mcp_summary(
    db: Any = Depends(get_db_session),
    risk_tier: Optional[RiskTierEnum] = Query(
        None,
        description="Optional filter to retrieve MCPs only of a specific risk tier."
    ),
    skip: int = Query(
        0,
        ge=0,
        description="The number of items to skip before starting to collect the result set."
    ),
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="The maximum number of items to return."
    )
) -> List[MCPSummaryResponse]:
    """
    Endpoint to get a summary of MCP servers with filtering and pagination.
    """
    # In a real application, 'db' would be a database session object
    # and 'all_mcp_records' would be the result of a database query.
    # For this task, 'db' is expected to be a list of MockMCPDBRecord objects
    # provided by the overridden dependency in the __main__ block.
    all_mcp_records: List[MockMCPDBRecord] = db

    results: List[MCPSummaryResponse] = []
    for record in all_mcp_records:
        calculated_risk_tier = assign_risk_tier(record.overall_risk)
        # Apply risk_tier filter if provided
        if risk_tier is None or calculated_risk_tier == risk_tier:
            results.append(
                MCPSummaryResponse(
                    mcp_name=record.mcp_name,
                    overall_risk=record.overall_risk,
                    risk_tier=calculated_risk_tier
                )
            )

    # Apply pagination
    paginated_results = results[skip : skip + limit]

    return paginated_results

# --- Acceptance Tests (using FastAPI TestClient) ---

if __name__ == "__main__":
    print("Running acceptance tests for /mcp_summary endpoint...")

    # Seed an in-memory store (mock database data)
    mock_db_data: List[MockMCPDBRecord] = [
        MockMCPDBRecord(mcp_name="MCP-Alpha", overall_risk=0.15),  # LOW
        MockMCPDBRecord(mcp_name="MCP-Beta", overall_risk=0.45),   # MEDIUM
        MockMCPDBRecord(mcp_name="MCP-Gamma", overall_risk=0.75),  # HIGH
        MockMCPDBRecord(mcp_name="MCP-Delta", overall_risk=0.95),  # CRITICAL
        MockMCPDBRecord(mcp_name="MCP-Epsilon", overall_risk=0.28), # LOW
        MockMCPDBRecord(mcp_name="MCP-Zeta", overall_risk=0.59),   # MEDIUM
        MockMCPDBRecord(mcp_name="MCP-Eta", overall_risk=0.88),    # HIGH
        MockMCPDBRecord(mcp_name="MCP-Theta", overall_risk=0.05),  # LOW
    ]

    # Override the get_db_session dependency for testing purposes
    def override_get_db_session() -> Generator[List[MockMCPDBRecord], None, None]:
        """Yields the mock in-memory database data for testing."""
        yield mock_db_data

    # Create a FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)
    # Apply the dependency override
    app.dependency_overrides[get_db_session] = override_get_db_session

    # Initialize the TestClient
    client = TestClient(app)

    # Test Case 1: Basic retrieval - all MCPs
    response = client.get("/mcp_summary")
    assert response.status_code == 200, f"Test 1 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 8, f"Test 1 Failed: Expected 8 MCPs, got {len(data)}"
    assert all(isinstance(item, dict) for item in data), "Test 1 Failed: All items should be dictionaries"
    assert all(
        "mcp_name" in item and "overall_risk" in item and "risk_tier" in item
        for item in data
    ), "Test 1 Failed: Each item should have mcp_name, overall_risk, and risk_tier"
    assert data[0]["mcp_name"] == "MCP-Alpha"
    assert data[0]["risk_tier"] == RiskTierEnum.LOW.value
    print("Test Case 1 (Basic retrieval) PASSED")

    # Test Case 2: Filter by risk_tier = LOW
    response = client.get("/mcp_summary?risk_tier=LOW")
    assert response.status_code == 200, f"Test 2 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 3, f"Test 2 Failed: Expected 3 LOW risk MCPs, got {len(data)}"
    assert all(item["risk_tier"] == RiskTierEnum.LOW.value for item in data)
    assert {item["mcp_name"] for item in data} == {"MCP-Alpha", "MCP-Epsilon", "MCP-Theta"}
    print("Test Case 2 (Filter by LOW risk) PASSED")

    # Test Case 3: Filter by risk_tier = HIGH
    response = client.get("/mcp_summary?risk_tier=HIGH")
    assert response.status_code == 200, f"Test 3 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 2, f"Test 3 Failed: Expected 2 HIGH risk MCPs, got {len(data)}"
    assert all(item["risk_tier"] == RiskTierEnum.HIGH.value for item in data)
    assert {item["mcp_name"] for item in data} == {"MCP-Gamma", "MCP-Eta"}
    print("Test Case 3 (Filter by HIGH risk) PASSED")

    # Test Case 4: Filter by risk_tier = CRITICAL
    response = client.get("/mcp_summary?risk_tier=CRITICAL")
    assert response.status_code == 200, f"Test 4 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 1, f"Test 4 Failed: Expected 1 CRITICAL risk MCP, got {len(data)}"
    assert data[0]["risk_tier"] == RiskTierEnum.CRITICAL.value
    assert data[0]["mcp_name"] == "MCP-Delta"
    print("Test Case 4 (Filter by CRITICAL risk) PASSED")

    # Test Case 5: Pagination - skip 2, limit 3
    response = client.get("/mcp_summary?skip=2&limit=3")
    assert response.status_code == 200, f"Test 5 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 3, f"Test 5 Failed: Expected 3 items, got {len(data)}"
    assert data[0]["mcp_name"] == "MCP-Gamma" # 3rd item in original list
    assert data[1]["mcp_name"] == "MCP-Delta" # 4th item
    assert data[2]["mcp_name"] == "MCP-Epsilon" # 5th item
    print("Test Case 5 (Pagination) PASSED")

    # Test Case 6: Pagination with filter - skip 1, limit 1, risk_tier=MEDIUM
    response = client.get("/mcp_summary?risk_tier=MEDIUM&skip=1&limit=1")
    assert response.status_code == 200, f"Test 6 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 1, f"Test 6 Failed: Expected 1 item, got {len(data)}"
    assert data[0]["mcp_name"] == "MCP-Zeta" # Second MEDIUM item
    assert data[0]["risk_tier"] == RiskTierEnum.MEDIUM.value
    print("Test Case 6 (Pagination with filter) PASSED")

    # Test Case 7: Invalid risk_tier filter value
    response = client.get("/mcp_summary?risk_tier=INVALID_TIER")
    assert response.status_code == 422, f"Test 7 Failed: Expected status 422 for invalid enum, got {response.status_code}"
    print("Test Case 7 (Invalid risk_tier filter value) PASSED")

    # Test Case 8: Pagination beyond available data
    response = client.get("/mcp_summary?skip=100&limit=10")
    assert response.status_code == 200, f"Test 8 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert len(data) == 0, f"Test 8 Failed: Expected 0 items, got {len(data)}"
    print("Test Case 8 (Pagination beyond available data) PASSED")

    print("\nAll acceptance tests PASSED")