# mcp_policy_rules_management_api.py
import json
import requests
from fastapi import FastAPI, APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional

# Configuration for the write_service
WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Pydantic models
class PolicyRuleCreate(BaseModel):
    """
    Model for creating a new policy rule.
    """
    rule_type: str = Field(..., example="deny_list", description="The type of the policy rule (e.g., 'deny_list', 'allow_list').")
    pattern: str = Field(..., example="malicious_domain.com", description="The pattern associated with the rule (e.g., a domain, a regex).")

class PolicyRuleInDB(PolicyRuleCreate):
    """
    Model for a policy rule as stored in the database, including its ID.
    """
    id: int = Field(..., example=1, description="The unique identifier of the policy rule.")

# FastAPI Router
router = APIRouter()

# Helper functions for interacting with write_service
def _call_write_service(endpoint: str, payload: dict):
    """
    Generic function to call the write_service with a given endpoint and payload.
    Handles common request exceptions and returns JSON response.
    """
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/{endpoint}",
            json=payload,
            timeout=5  # 5-second timeout for requests
        )
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        return response.json()
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to write_service at {WRITE_SERVICE_URL}. Error: {e}"
        )
    except requests.exceptions.Timeout as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Timeout connecting to write_service at {WRITE_SERVICE_URL}. Error: {e}"
        )
    except requests.exceptions.RequestException as e:
        # Catch other requests-related errors
        error_detail = f"Error interacting with write_service: {e}"
        if 'response' in locals() and response is not None:
            error_detail += f". Response: {response.text}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail
        )
    except json.JSONDecodeError:
        # Handle cases where the response is not valid JSON
        error_detail = "Failed to decode JSON response from write_service."
        if 'response' in locals() and response is not None:
            error_detail += f" Response: {response.text}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail
        )

def execute_query(query: str, params: Optional[List] = None):
    """
    Executes a DDL/DML query that might return affected rows or a single result (e.g., with RETURNING).
    """
    payload = {"query": query, "params": params if params is not None else []}
    return _call_write_service("execute_query", payload)

def fetch_all(query: str, params: Optional[List] = None):
    """
    Fetches all rows from a SELECT query.
    """
    payload = {"query": query, "params": params if params is not None else []}
    return _call_write_service("fetch_all", payload)

@router.post("/policy_rules", response_model=PolicyRuleInDB, status_code=status.HTTP_201_CREATED)
async def create_policy_rule(rule: PolicyRuleCreate):
    """
    Adds a new policy rule to the system.

    Accepts a JSON payload with `rule_type` and `pattern`.
    The rule is stored in the `mcp_policy_rules` table.
    Returns the newly created rule, including its generated `id`.
    """
    insert_query = "INSERT INTO mcp_policy_rules (rule_type, pattern) VALUES (?, ?) RETURNING id, rule_type, pattern;"
    params = [rule.rule_type, rule.pattern]

    response_data = execute_query(insert_query, params)

    # write_service is expected to return {"data": [...]}
    if not response_data or not response_data.get("data"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create policy rule: No data returned from write_service after insertion."
        )
    
    # Assuming write_service returns a list of dictionaries for RETURNING clause
    created_rule_data = response_data["data"][0]
    return PolicyRuleInDB(**created_rule_data)

@router.get("/policy_rules", response_model=List[PolicyRuleInDB])
async def list_policy_rules():
    """
    Retrieves a list of all existing policy rules.

    Returns a list of policy rules, each including its `id`, `rule_type`, and `pattern`.
    """
    select_query = "SELECT id, rule_type, pattern FROM mcp_policy_rules;"
    response_data = fetch_all(select_query)

    # write_service is expected to return {"data": [...]}
    if not response_data or not response_data.get("data"):
        return [] # Return empty list if no data or empty data from write_service
    
    return [PolicyRuleInDB(**rule_data) for rule_data in response_data["data"]]

# Main application for testing
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    import uvicorn

    # --- Mock Write Service for Acceptance Testing ---
    # This mock simulates the behavior of the external write_service
    # without needing it to be actually running.
    class MockWriteService:
        def __init__(self):
            self.db = [] # Simulate a list of dictionaries as a database table
            self.next_id = 1
            self.init_db_schema()

        def init_db_schema(self):
            # In a real scenario, this would be a DDL query to create the table.
            # For the mock, we just ensure the 'table' is ready for inserts.
            print("MockWriteService: Initializing mcp_policy_rules table (in-memory).")

        def execute_query(self, query: str, params: List):
            query_lower = query.strip().lower()
            if query_lower.startswith("insert into mcp_policy_rules"):
                rule_type, pattern = params
                new_rule = {"id": self.next_id, "rule_type": rule_type, "pattern": pattern}
                self.db.append(new_rule)
                self.next_id += 1
                print(f"MockWriteService: Inserted {new_rule}")
                return {"data": [new_rule]} # Simulate RETURNING clause
            elif query_lower.startswith("delete from mcp_policy_rules"):
                # Simple delete for cleanup in tests
                self.db = []
                self.next_id = 1
                print("MockWriteService: Cleared mcp_policy_rules table.")
                return {"data": []}
            else:
                raise ValueError(f"Unsupported query in mock execute_query: {query}")

        def fetch_all(self, query: str, params: List):
            query_lower = query.strip().lower()
            if query_lower.startswith("select id, rule_type, pattern from mcp_policy_rules"):
                print(f"MockWriteService: Fetching all rules: {self.db}")
                return {"data": self.db}
            else:
                raise ValueError(f"Unsupported query in mock fetch_all: {query}")

    # Instantiate the mock service
    mock_service = MockWriteService()

    # Override the actual helper functions to use the mock service for testing
    # This is a common pattern for unit/integration testing external dependencies.
    _original_execute_query = globals()['execute_query']
    _original_fetch_all = globals()['fetch_all']

    globals()['execute_query'] = lambda q, p=None: mock_service.execute_query(q, p if p is not None else [])
    globals()['fetch_all'] = lambda q, p=None: mock_service.fetch_all(q, p if p is not None else [])

    # --- FastAPI Test Client Setup ---
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    print("Running acceptance tests for mcp_policy_rules_management_api.py...")

    try:
        # 1. Ensure a clean state before tests
        mock_service.execute_query("DELETE FROM mcp_policy_rules;", [])
        print("Initial cleanup complete.")

        # 2. Test POST /policy_rules - Add a new rule
        test_rule_1_data = {"rule_type": "deny_list", "pattern": "bad_domain.com"}
        print(f"\nTesting POST /policy_rules with: {test_rule_1_data}")
        response = client.post("/policy_rules", json=test_rule_1_data)

        assert response.status_code == status.HTTP_201_CREATED, \
            f"FAIL: Expected 201 Created, got {response.status_code}. Response: {response.json()}"
        
        created_rule_1 = response.json()
        assert "id" in created_rule_1, "FAIL: Created rule should have an 'id' field."
        assert created_rule_1["rule_type"] == test_rule_1_data["rule_type"], \
            f"FAIL: Rule type mismatch. Expected '{test_rule_1_data['rule_type']}', got '{created_rule_1['rule_type']}'."
        assert created_rule_1["pattern"] == test_rule_1_data["pattern"], \
            f"FAIL: Pattern mismatch. Expected '{test_rule_1_data['pattern']}', got '{created_rule_1['pattern']}'."
        print(f"POST /policy_rules (Rule 1) test passed. Created rule: {created_rule_1}")

        # 3. Test GET /policy_rules - Retrieve all rules and verify Rule 1
        print("\nTesting GET /policy_rules to retrieve all rules.")
        response = client.get("/policy_rules")

        assert response.status_code == status.HTTP_200_OK, \
            f"FAIL: Expected 200 OK, got {response.status_code}. Response: {response.json()}"
        
        retrieved_rules = response.json()
        assert isinstance(retrieved_rules, list), "FAIL: GET /policy_rules should return a list."
        assert len(retrieved_rules) == 1, f"FAIL: Expected 1 rule, got {len(retrieved_rules)}."
        
        retrieved_rule_1 = retrieved_rules[0]
        assert retrieved_rule_1 == created_rule_1, \
            f"FAIL: Retrieved rule {retrieved_rule_1} does not match created rule {created_rule_1}."
        print(f"GET /policy_rules (after Rule 1) test passed. Retrieved rules: {retrieved_rules}")

        # 4. Test POST /policy_rules - Add a second rule
        test_rule_2_data = {"rule_type": "allow_list", "pattern": "safe_pattern.*"}
        print(f"\nTesting POST /policy_rules with: {test_rule_2_data}")
        response = client.post("/policy_rules", json=test_rule_2_data)
        assert response.status_code == status.HTTP_201_CREATED, \
            f"FAIL: Expected 201 Created, got {response.status_code}. Response: {response.json()}"
        
        created_rule_2 = response.json()
        assert "id" in created_rule_2, "FAIL: Created rule 2 should have an 'id' field."
        assert created_rule_2["rule_type"] == test_rule_2_data["rule_type"]
        assert created_rule_2["pattern"] == test_rule_2_data["pattern"]
        assert created_rule_2["id"] != created_rule_1["id"], "FAIL: Second rule should have a different ID."
        print(f"POST /policy_rules (Rule 2) test passed. Created rule: {created_rule_2}")

        # 5. Test GET /policy_rules - Retrieve all rules and verify both rules
        print("\nTesting GET /policy_rules to retrieve all rules after adding a second one.")
        response = client.get("/policy_rules")
        assert response.status_code == status.HTTP_200_OK, \
            f"FAIL: Expected 200 OK, got {response.status_code}. Response: {response.json()}"
        
        retrieved_rules_after_second = response.json()
        assert len(retrieved_rules_after_second) == 2, \
            f"FAIL: Expected 2 rules, got {len(retrieved_rules_after_second)}."
        
        # Check if both created rules are present in the retrieved list
        assert created_rule_1 in retrieved_rules_after_second, "FAIL: Rule 1 not found in retrieved list."
        assert created_rule_2 in retrieved_rules_after_second, "FAIL: Rule 2 not found in retrieved list."
        print(f"GET /policy_rules (after Rule 2) test passed. Retrieved rules: {retrieved_rules_after_second}")

        print("\nAll acceptance tests passed successfully!")
        print("PASS")

    except AssertionError as e:
        print(f"\nFAIL: Acceptance test failed - {e}")
    except Exception as e:
        print(f"\nFAIL: An unexpected error occurred during testing - {e}")
    finally:
        # Restore original functions to avoid side effects if this module is imported elsewhere
        globals()['execute_query'] = _original_execute_query
        globals()['fetch_all'] = _original_fetch_all
        print("\nMock functions restored to original implementations.")

    # Optional: If you want to run the FastAPI app normally (e.g., for manual testing)
    # after the tests, you can uncomment the following:
    # print("\nStarting FastAPI application (http://127.0.0.1:8000)...")
    # uvicorn.run(app, host="127.0.0.1", port=8000)