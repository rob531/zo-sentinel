import os
import requests
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any

# --- Configuration ---
# Default URL for the write service. Can be overridden by environment variable.
# Example: export WRITE_SERVICE_URL="http://your-write-service:8001"
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8001")

# --- FastAPI Router Setup ---
router = APIRouter()

# --- Pydantic Models for API Response ---
class ViolatedRule(BaseModel):
    """Represents a policy rule that has been violated."""
    rule_id: str
    description: str

class PolicyEvaluationResult(BaseModel):
    """The complete result of an MCP policy evaluation."""
    mcp_id: str
    compliance_status: str # "COMPLIANT" or "NON_COMPLIANT"
    violated_rules: List[ViolatedRule]

# --- Helper Function for Rule Evaluation ---
def evaluate_rule(condition: str, mcp_data: Dict[str, Any]) -> bool:
    """
    Evaluates a policy rule condition against MCP data.
    Returns True if the condition is met (i.e., rule is violated), False otherwise.
    
    The evaluation is performed in a restricted environment to enhance security.
    Only 'mcp_data' is available in the local scope, and built-in functions are disabled.
    """
    # Create a safe execution environment for eval()
    # Restrict built-ins and provide only 'mcp_data' in the local scope.
    safe_globals = {"__builtins__": None}
    safe_locals = {"mcp_data": mcp_data}
    try:
        # Evaluate the condition string. If it evaluates to a truthy value, the rule is violated.
        return bool(eval(condition, safe_globals, safe_locals))
    except Exception as e:
        # Log the error for debugging purposes.
        # In a production environment, you might want more robust logging.
        print(f"Error evaluating condition '{condition}' for MCP {mcp_data.get('mcp_id')}: {e}")
        # If evaluation fails, we consider the rule not violated to avoid false positives
        # or unexpected behavior due to malformed rules.
        return False

# --- API Endpoint ---
@router.get(
    "/mcp/{mcp_id}/policy_evaluation",
    response_model=PolicyEvaluationResult,
    summary="Evaluate MCP policy compliance",
    description="Retrieves policy rules and MCP metadata, evaluates the MCP against the rules, "
                "and returns a compliance status and a list of violated rules."
)
async def get_policy_evaluation(mcp_id: str):
    """
    Endpoint to evaluate a specific MCP against all defined policy rules.

    - **mcp_id**: The unique identifier of the MCP to evaluate.
    """
    # 1. Retrieve Policy Rules from the write_service
    try:
        policy_rules_response = requests.get(f"{WRITE_SERVICE_URL}/policy_rules")
        policy_rules_response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        policy_rules = policy_rules_response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve policy rules from write_service at {WRITE_SERVICE_URL}/policy_rules: {e}"
        )

    # 2. Retrieve MCP Metadata from the write_service
    try:
        mcp_data_response = requests.get(f"{WRITE_SERVICE_URL}/mcp/{mcp_id}")
        mcp_data_response.raise_for_status()
        mcp_data = mcp_data_response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"MCP with ID '{mcp_id}' not found.")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve MCP metadata for '{mcp_id}' from write_service at {WRITE_SERVICE_URL}/mcp/{mcp_id}: {e}"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to write_service to retrieve MCP metadata for '{mcp_id}': {e}"
        )
    
    if not mcp_data: # Should be caught by 404 above, but as a safeguard
        raise HTTPException(status_code=404, detail=f"MCP with ID '{mcp_id}' not found or returned empty data.")

    # 3. Evaluate MCP against Policy Rules
    violated_rules: List[ViolatedRule] = []
    for rule in policy_rules:
        rule_id = rule.get("rule_id")
        description = rule.get("description")
        condition = rule.get("condition")

        if not all([rule_id, description, condition]):
            print(f"Warning: Malformed policy rule skipped: {rule}")
            continue

        if evaluate_rule(condition, mcp_data):
            violated_rules.append(ViolatedRule(rule_id=rule_id, description=description))

    # 4. Determine Compliance Status
    compliance_status = "NON_COMPLIANT" if violated_rules else "COMPLIANT"

    return PolicyEvaluationResult(
        mcp_id=mcp_id,
        compliance_status=compliance_status,
        violated_rules=violated_rules
    )

# --- Main Application (for local development/testing) ---
app = FastAPI(
    title="MCP Policy Evaluator API",
    description="API for evaluating MCPs against defined policy rules.",
    version="1.0.0",
)
app.include_router(router)


# --- __main__ block for testing with TestClient ---
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    import requests_mock
    import json

    print("Running __main__ block for testing...")

    # Create a test client for the FastAPI app
    client = TestClient(app)

    # Define mock data for the write_service
    MOCK_POLICY_RULES = [
        {"rule_id": "RULE-001", "description": "MCP status must not be 'draft'", "condition": "mcp_data['status'] == 'draft'"},
        {"rule_id": "RULE-002", "description": "MCP priority must be greater than 5", "condition": "mcp_data['priority'] <= 5"},
        {"rule_id": "RULE-003", "description": "MCP owner must not be 'admin'", "condition": "mcp_data['owner'] == 'admin'"},
        {"rule_id": "RULE-004", "description": "MCP must have at least one tag", "condition": "not mcp_data.get('tags') or len(mcp_data['tags']) == 0"},
        {"rule_id": "RULE-005", "description": "MCP creation date must be after 2023-01-01", "condition": "mcp_data.get('created_at', '1970-01-01') < '2023-01-01'"},
        {"rule_id": "RULE-006", "description": "MCP must have a 'critical' tag if priority is 1", "condition": "mcp_data['priority'] == 1 and 'critical' not in mcp_data.get('tags', [])"},
    ]

    # Test Case 1: Compliant MCP
    MOCK_MCP_COMPLIANT = {
        "mcp_id": "mcp-compliant-123",
        "status": "approved",
        "priority": 10,
        "owner": "user1",
        "tags": ["tagA", "tagB"],
        "created_at": "2023-06-15"
    }
    EXPECTED_COMPLIANT_RESULT = {
        "mcp_id": "mcp-compliant-123",
        "compliance_status": "COMPLIANT",
        "violated_rules": []
    }

    # Test Case 2: Non-Compliant MCP (violates RULE-001, RULE-002, RULE-003, RULE-004, RULE-005, RULE-006)
    MOCK_MCP_NON_COMPLIANT = {
        "mcp_id": "mcp-noncompliant-456",
        "status": "draft", # Violates RULE-001
        "priority": 1,    # Violates RULE-002 (1 <= 5) and RULE-006 (priority 1 but no 'critical' tag)
        "owner": "admin", # Violates RULE-003
        "tags": [],       # Violates RULE-004
        "created_at": "2022-12-01" # Violates RULE-005
    }
    EXPECTED_NON_COMPLIANT_RESULT = {
        "mcp_id": "mcp-noncompliant-456",
        "compliance_status": "NON_COMPLIANT",
        "violated_rules": [
            {"rule_id": "RULE-001", "description": "MCP status must not be 'draft'"},
            {"rule_id": "RULE-002", "description": "MCP priority must be greater than 5"},
            {"rule_id": "RULE-003", "description": "MCP owner must not be 'admin'"},
            {"rule_id": "RULE-004", "description": "MCP must have at least one tag"},
            {"rule_id": "RULE-005", "description": "MCP creation date must be after 2023-01-01"},
            {"rule_id": "RULE-006", "description": "MCP must have a 'critical' tag if priority is 1"},
        ]
    }

    # Test Case 3: MCP Not Found
    MOCK_MCP_NOT_FOUND_ID = "mcp-not-found-789"

    # Use requests_mock to mock the external HTTP calls
    with requests_mock.Mocker() as m:
        # Mock the policy rules endpoint
        m.get(f"{WRITE_SERVICE_URL}/policy_rules", json=MOCK_POLICY_RULES)

        # Mock compliant MCP data
        m.get(f"{WRITE_SERVICE_URL}/mcp/{MOCK_MCP_COMPLIANT['mcp_id']}", json=MOCK_MCP_COMPLIANT)
        
        # Mock non-compliant MCP data
        m.get(f"{WRITE_SERVICE_URL}/mcp/{MOCK_MCP_NON_COMPLIANT['mcp_id']}", json=MOCK_MCP_NON_COMPLIANT)

        # Mock MCP not found scenario
        m.get(f"{WRITE_SERVICE_URL}/mcp/{MOCK_MCP_NOT_FOUND_ID}", status_code=404, json={"detail": "MCP not found"})

        # --- Test Case 1: Compliant MCP ---
        print(f"\nTesting Compliant MCP: {MOCK_MCP_COMPLIANT['mcp_id']}")
        response = client.get(f"/mcp/{MOCK_MCP_COMPLIANT['mcp_id']}/policy_evaluation")
        assert response.status_code == 200, f"Expected status 200, got {response.status_code}. Response: {response.json()}"
        assert response.json() == EXPECTED_COMPLIANT_RESULT, f"Compliant MCP test failed. Expected: {EXPECTED_COMPLIANT_RESULT}, Got: {response.json()}"
        print(f"  Result: {response.json()}")
        print("  Compliant MCP test PASSED.")

        # --- Test Case 2: Non-Compliant MCP ---
        print(f"\nTesting Non-Compliant MCP: {MOCK_MCP_NON_COMPLIANT['mcp_id']}")
        response = client.get(f"/mcp/{MOCK_MCP_NON_COMPLIANT['mcp_id']}/policy_evaluation")
        assert response.status_code == 200, f"Expected status 200, got {response.status_code}. Response: {response.json()}"
        
        # Sort violated rules for comparison as order might not be guaranteed by `eval` loop
        response_json = response.json()
        response_json['violated_rules'] = sorted(response_json['violated_rules'], key=lambda x: x['rule_id'])