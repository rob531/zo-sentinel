# mcp_policy_rules_detail_api.py
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, APIRouter, HTTPException, status
from pydantic import BaseModel

# --- Mock write_service for demonstration and testing ---
# In a real application, this would be an actual import from your services layer:
# from zo_sentinel.services import write_service
class MockWriteService:
    """
    A mock for the write_service to simulate database interactions.
    This class mimics fetching a single row from a database table.
    """
    _data = {
        1: {
            "rule_type": "SQL_INJECTION",
            "pattern": "SELECT .* FROM .*",
            "severity": "HIGH",
            "description": "Detects common SQL injection patterns.",
            "created_at": datetime(2023, 1, 1, 10, 0, 0)
        },
        2: {
            "rule_type": "XSS",
            "pattern": "<script>.*</script>",
            "severity": "MEDIUM",
            "description": "Detects potential Cross-Site Scripting attempts.",
            "created_at": datetime(2023, 1, 5, 11, 30, 0)
        },
        3: {
            "rule_type": "COMMAND_INJECTION",
            "pattern": "exec\\(.*\\)",
            "severity": "CRITICAL",
            "description": "Identifies attempts to execute system commands.",
            "created_at": datetime(2023, 2, 10, 14, 15, 0)
        }
    }

    async def fetch_one(self, query: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Simulates fetching a single row from the database based on rule_id.
        """
        rule_id = params.get("rule_id")
        if rule_id in self._data:
            # Return a copy to prevent external modification of internal mock data
            return self._data[rule_id].copy()
        return None

# Instantiate the mock service. In a real application, this would be the actual
# write_service instance, potentially injected via dependency injection.
write_service = MockWriteService()

# --- Pydantic Model for Response ---
class MCPPolicyRuleDetail(BaseModel):
    """
    Pydantic model representing the detailed information of an MCP policy rule.
    """
    rule_type: str
    pattern: str
    severity: str
    description: str
    created_at: datetime

# --- FastAPI Application and Router ---
app = FastAPI(
    title="MCP Policy Rules Detail API",
    description="API for retrieving detailed information about MCP policy rules."
)
router = APIRouter()

@router.get(
    "/mcp_policy_rules/{rule_id}/detail",
    response_model=MCPPolicyRuleDetail,
    summary="Get detailed information for an MCP policy rule",
    description="Retrieves comprehensive details for a specific MCP policy rule by its ID from the `mcp_policy_rules` table."
)
async def get_mcp_policy_rule_detail(rule_id: int):
    """
    Retrieves detailed information for a specific MCP policy rule.

    Args:
        rule_id (int): The unique identifier of the MCP policy rule.

    Returns:
        MCPPolicyRuleDetail: The detailed information about the policy rule.

    Raises:
        HTTPException:
            - 404 Not Found: If no rule with the given `rule_id` is found in the database.
    """
    # Postgres-portable SQL query to select rule details
    query = """
        SELECT 
            rule_type, 
            pattern, 
            severity, 
            description, 
            created_at
        FROM 
            mcp_policy_rules
        WHERE 
            id = :rule_id;
    """
    
    # Execute the query using the write_service
    result = await write_service.fetch_one(query, {"rule_id": rule_id})

    if result is None:
        # If no rule is found, raise an HTTPException with 404 status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP policy rule with ID {rule_id} not found."
        )
    
    # Return the result, FastAPI will automatically validate and serialize it
    # using the `response_model` (MCPPolicyRuleDetail).
    return MCPPolicyRuleDetail(**result)

# Include the router in the main FastAPI application
app.include_router(router)

# --- Acceptance Criteria: __main__ block with TestClient ---
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Create a TestClient instance for the FastAPI app
    client = TestClient(app)

    print("--- Running Acceptance Tests for MCP Policy Rules Detail API ---")

    # Test Case 1: Successfully retrieve detailed data for a known rule_id (ID 1)
    print("\nTest Case 1: Retrieve known rule (ID 1)")
    response = client.get("/mcp_policy_rules/1/detail")
    
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()
    
    expected_data_1 = {
        "rule_type": "SQL_INJECTION",
        "pattern": "SELECT .* FROM .*",
        "severity": "HIGH",
        "description": "Detects common SQL injection patterns.",
        "created_at": "2023-01-01T10:00:00" # datetime objects are serialized to ISO 8601 strings
    }
    assert data == expected_data_1, f"Expected {expected_data_1}, got {data}"
    print("  ✅ Passed: Successfully retrieved rule details for ID 1.")

    # Test Case 2: Handle case with no rule found (ID 999)
    print("\nTest Case 2: Rule not found (ID 999)")
    response = client.get("/mcp_policy_rules/999/detail")
    
    assert response.status_code == 404, f"Expected status 404, got {response.status_code}"
    assert "MCP policy rule with ID 999 not found." in response.json()["detail"]
    print("  ✅ Passed: Correctly handled rule not found (404).")

    # Test Case 3: Handle invalid rule_id type (non-integer, e.g., 'abc')
    print("\nTest Case 3: Invalid rule_id type (e.g., 'abc')")
    response = client.get("/mcp_policy_rules/abc/detail")
    
    # FastAPI's path parameter validation automatically returns 422 for type mismatches
    assert response.status_code == 422, f"Expected status 422, got {response.status_code}"
    assert "value is not a valid integer" in response.json()["detail"][0]["msg"]
    print("  ✅ Passed: Correctly handled invalid rule_id type (422).")

    # Test Case 4: Retrieve another known rule (ID 2)
    print("\nTest Case 4: Retrieve another known rule (ID 2)")
    response = client.get("/mcp_policy_rules/2/detail")
    
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()
    
    expected_data_2 = {
        "rule_type": "XSS",
        "pattern": "<script>.*</script>",
        "severity": "MEDIUM",
        "description": "Detects potential Cross-Site Scripting attempts.",
        "created_at": "2023-01-05T11:30:00"
    }
    assert data == expected_data_2, f"Expected {expected_data_2}, got {data}"
    print("  ✅ Passed: Successfully retrieved rule details for ID 2.")

    print("\n--- All Acceptance Tests Completed Successfully ---")