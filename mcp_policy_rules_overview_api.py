# mcp_policy_rules_overview_api.py

from datetime import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

# --- Pydantic Models ---

class PolicyRuleOverview(BaseModel):
    """
    Represents a summary of an active policy rule.
    """
    rule_type: str = Field(..., description="The type of the policy rule (e.g., ALLOW, BLOCK).")
    pattern: str = Field(..., description="The pattern associated with the policy rule (e.g., '*.example.com').")
    timestamp: datetime = Field(..., description="The creation timestamp of the policy rule.")

class PolicyRulesOverviewResponse(BaseModel):
    """
    Response model for the policy rules overview endpoint.
    Contains a list of policy rule summaries.
    """
    rules: List[PolicyRuleOverview] = Field(..., description="A list of active policy rules.")

# --- Database Dependency Interface ---

class WriteService:
    """
    Abstract interface for the write_service, which handles database interactions.
    In a real application, this would be an actual service with database connection logic.
    For this task, it's defined to allow mocking in tests.
    """
    async def query(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        Executes a SQL query and returns the results as a list of dictionaries.
        This method is expected to be implemented by the actual database service.
        """
        raise NotImplementedError("The actual WriteService implementation must provide this method.")

# Dependency injector for the write_service
async def get_write_service() -> WriteService:
    """
    Provides an instance of the WriteService.
    This function will be overridden in tests.
    """
    # In a real application, this would return an actual WriteService instance
    # configured with a database connection pool.
    return WriteService()

# --- FastAPI Router ---

router = APIRouter()

@router.get(
    "/mcp/policy_rules/overview",
    response_model=PolicyRulesOverviewResponse,
    summary="Get an overview of active MCP policy rules",
    description="Retrieves a summary of active policy rules, including their type, pattern, and creation timestamp. "
                "The rules are ordered by timestamp in descending order (most recent first)."
)
async def get_policy_rules_overview(
    write_service: WriteService = Depends(get_write_service)
) -> PolicyRulesOverviewResponse:
    """
    Endpoint to retrieve an overview of active policy rules.

    Reads the `mcp_policy_rules` table and returns a list of
    `PolicyRuleOverview` objects. Handles cases where the table is empty.
    """
    sql_query = """
        SELECT rule_type, pattern, timestamp
        FROM mcp_policy_rules
        ORDER BY timestamp DESC;
    """
    try:
        results = await write_service.query(sql_query)
    except Exception as e:
        # In a real application, detailed logging would be performed here.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve policy rules overview due to a database error."
        )

    if not results:
        return PolicyRulesOverviewResponse(rules=[])

    rules_overview = [
        PolicyRuleOverview(
            rule_type=row["rule_type"],
            pattern=row["pattern"],
            timestamp=row["timestamp"]
        )
        for row in results
    ]
    return PolicyRulesOverviewResponse(rules=rules_overview)

# --- Self-Test Block ---

if __name__ == "__main__":
    app = FastAPI()
    app.include_router(router)

    # Mock WriteService for testing purposes
    class MockWriteServiceForTest:
        def __init__(self, data: List[Dict]):
            self._data = data

        async def query(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
            """
            Simulates fetching data from an in-memory store.
            For this specific endpoint, it returns the pre-seeded data directly,
            as the SQL query is a simple SELECT without complex filtering.
            """
            # In a more complex mock, one might parse the SQL to filter/order.
            # Here, we assume the seeded data is already in the expected format
            # and order if the SQL implies it (e.g., ORDER BY timestamp DESC).
            # The seeded data below is ordered by timestamp DESC for simplicity.
            return self._data

    # Dependency override function for TestClient
    def override_get_write_service(mock_data: List[Dict]):
        async def _get_mock_service():
            return MockWriteServiceForTest(mock_data)
        return _get_mock_service

    client = TestClient(app)

    # --- Test Case 1: Database with seeded data ---
    seeded_data = [
        {"rule_type": "BLOCK", "pattern": "bad.domain.net", "timestamp": datetime(2023, 1, 2, 11, 30, 0)},
        {"rule_type": "ALLOW", "pattern": "*.example.com", "timestamp": datetime(2023, 1, 1, 10, 0, 0)},
    ]
    app.dependency_overrides[get_write_service] = override_get_write_service(seeded_data)

    print("Running Test Case 1: Database with seeded data...")
    response = client.get("/mcp/policy_rules/overview")

    assert response.status_code == status.HTTP_200_OK, \
        f"Test Case 1 Failed: Expected 200, got {response.status_code}"
    response_json = response.json()
    assert "rules" in response_json, \
        f"Test Case 1 Failed: 'rules' key missing in response."
    assert len(response_json["rules"]) == 2, \
        f"Test Case 1 Failed: Expected 2 rules, got {len(response_json['rules'])}"

    # Assert specific rule data, considering the ORDER BY timestamp DESC
    assert response_json["rules"][0]["rule_type"] == "BLOCK"
    assert response_json["rules"][0]["pattern"] == "bad.domain.net"
    assert response_json["rules"][0]["timestamp"] == "2023-01-02T11:30:00"

    assert response_json["rules"][1]["rule_type"] == "ALLOW"
    assert response_json["rules"][1]["pattern"] == "*.example.com"
    assert response_json["rules"][1]["timestamp"] == "2023-01-01T10:00:00"

    print("Test Case 1 (with data): PASS")

    # --- Test Case 2: Empty database ---
    empty_data: List[Dict] = []
    app.dependency_overrides[get_write_service] = override_get_write_service(empty_data)

    print("\nRunning Test Case 2: Empty database...")
    response = client.get("/mcp/policy_rules/overview")

    assert response.status_code == status.HTTP_200_OK, \
        f"Test Case 2 Failed: Expected 200, got {response.status_code}"
    response_json = response.json()
    assert "rules" in response_json, \
        f"Test Case 2 Failed: 'rules' key missing in response."
    assert len(response_json["rules"]) == 0, \
        f"Test Case 2 Failed: Expected 0 rules, got {len(response_json['rules'])}"

    print("Test Case 2 (empty data): PASS")

    print("\nAll tests passed: PASS")