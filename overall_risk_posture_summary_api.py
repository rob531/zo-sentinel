import json
from typing import Dict, Any, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Initialize FastAPI application
app = FastAPI()

# PRODUCT_SPEC §2: Defined Risk Tiers
# These are the canonical risk tiers that the summary should report on.
RISK_TIERS = [
    "TRUSTED_GENERAL",
    "HIGH_RISK_ISOLATED",
    "CRITICAL_RISK_ISOLATED",
    "UNCLASSIFIED",
]

# --- Mock Database Interaction ---
# In a real application, this would be an actual client for a database service
# (e.g., an HTTP client for a `write_service` or a direct DB driver).
# For this task, we simulate the `mcp_risk_register` table data and a `query` function.

# Mock data representing the `mcp_risk_register` table.
# Each dictionary represents a row in the table, with at least an `mcp_id` and `risk_tier`.
_MOCK_MCP_RISK_REGISTER_DATA = [
    {"mcp_id": "mcp-1", "risk_tier": "TRUSTED_GENERAL"},
    {"mcp_id": "mcp-2", "risk_tier": "HIGH_RISK_ISOLATED"},
    {"mcp_id": "mcp-3", "risk_tier": "TRUSTED_GENERAL"},
    {"mcp_id": "mcp-4", "risk_tier": "CRITICAL_RISK_ISOLATED"},
    {"mcp_id": "mcp-5", "risk_tier": "TRUSTED_GENERAL"},
    {"mcp_id": "mcp-6", "risk_tier": "HIGH_RISK_ISOLATED"},
    {"mcp_id": "mcp-7", "risk_tier": "UNCLASSIFIED"},
    {"mcp_id": "mcp-8", "risk_tier": "TRUSTED_GENERAL"},
    # Include an MCP with a tier not explicitly defined in RISK_TIERS
    # to ensure robustness and correct overall count calculation.
    {"mcp_id": "mcp-9", "risk_tier": "UNKNOWN_RISK_TIER"},
    {"mcp_id": "mcp-10", "risk_tier": "TRUSTED_GENERAL"},
]

async def query(sql: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Simulates querying a database service (e.g., `write_service /query`).
    This function adheres to the requirement of using parameterized SQL queries.
    For this specific task, it simulates fetching `risk_tier` from `mcp_risk_register`.

    Args:
        sql (str): The SQL query string.
        params (Dict[str, Any], optional): A dictionary of parameters for the SQL query.
                                           Defaults to None.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents a row
                              returned by the query.
    """
    # In a real scenario, this function would make an HTTP call to a service endpoint
    # (e.g., POST to `/write_service/query` with SQL and params in the body)
    # or use a direct database client to execute the SQL with parameters.
    
    # For the purpose of this mock, we'll simulate fetching data from our in-memory table.
    # We perform a very basic check on the SQL to determine what data to return.
    if "SELECT risk_tier FROM mcp_risk_register" in sql.upper():
        # Simulate fetching all risk tiers from the mock table.
        # In a real DB, this would be the result of executing the SQL.
        return [{"risk_tier": row["risk_tier"]} for row in _MOCK_MCP_RISK_REGISTER_DATA]
    
    # If the SQL is not recognized by our mock, return an empty list.
    print(f"Mock query received unrecognized SQL: {sql} with params: {params}")
    return []

# --- FastAPI Endpoint ---

@app.get("/risk_posture/summary", response_model=Dict[str, Any])
async def get_risk_posture_summary() -> Dict[str, Any]:
    """
    Returns a high-level summary of the overall risk posture across all MCPs.
    This API queries the `mcp_risk_register` table to calculate the count of MCPs
    in each risk tier as defined in PRODUCT_SPEC §2.
    The output is a JSON object mapping each risk tier to its count,
    and an overall count of MCPs.
    """
    # SQL query to fetch all risk tiers from the `mcp_risk_register` table.
    # This uses parameterized SQL, even though `params` will be empty for this simple SELECT.
    sql_query = "SELECT risk_tier FROM mcp_risk_register;"
    
    # Execute the query via the simulated database service.
    # The `params` argument is explicitly passed to adhere to the "parameterized SQL queries"
    # requirement, even if it's an empty dictionary for this particular query.
    results = await query(sql_query, params={})
    
    # Initialize counts for all defined risk tiers to 0.
    risk_tier_counts: Dict[str, int] = {tier: 0 for tier in RISK_TIERS}
    overall_mcp_count = 0
    
    # Process the query results to populate counts.
    for row in results:
        tier = row.get("risk_tier")
        if tier in risk_tier_counts:
            # Increment count only for defined risk tiers.
            risk_tier_counts[tier] += 1
        
        # Increment overall count for every MCP found, regardless of its tier classification.
        overall_mcp_count += 1
            
    # Combine individual tier counts with the overall MCP count into the final summary.
    summary = {**risk_tier_counts, "overall_mcp_count": overall_mcp_count}
    
    return summary

# --- Acceptance Test Block ---

if __name__ == "__main__":
    # Create a TestClient for the FastAPI application
    client = TestClient(app)
    
    print("--- Running acceptance test for /risk_posture/summary ---")
    
    # Call the /risk_posture/summary endpoint
    response = client.get("/risk_posture/summary")
    
    # Assert the HTTP status code is 200 (OK)
    assert response.status_code == 200, \
        f"FAIL: Expected status code 200, got {response.status_code}. Response: {response.text}"
    
    # Parse the JSON response
    summary_data = response.json()
    
    print(f"Received summary:\n{json.dumps(summary_data, indent=2)}")
    
    # Assert all defined risk tiers are present in the response and have non-negative integer counts
    for tier in RISK_TIERS:
        assert tier in summary_data, \
            f"FAIL: Risk tier '{tier}' not found in response."
        assert isinstance(summary_data[tier], int), \
            f"FAIL: Count for '{tier}' is not an integer. Got: {type(summary_data[tier])}"
        assert summary_data[tier] >= 0, \
            f"FAIL: Count for '{tier}' is negative: {summary_data[tier]}"
            
    # Assert 'overall_mcp_count' is present and has a non-negative integer count
    assert "overall_mcp_count" in summary_data, \
        "FAIL: 'overall_mcp_count' not found in response."
    assert isinstance(summary_data["overall_mcp_count"], int), \
        f"FAIL: 'overall_mcp_count' is not an integer. Got: {type(summary_data['overall_mcp_count'])}"
    assert summary_data["overall_mcp_count"] >= 0, \
        f"FAIL: 'overall_mcp_count' is negative: {summary_data['overall_mcp_count']}"
    
    # Verify the overall count matches the total number of MCPs in the mock data
    expected_overall_count = len(_MOCK_MCP_RISK_REGISTER_DATA)
    assert summary_data["overall_mcp_count"] == expected_overall_count, \
        f"FAIL: Overall MCP count mismatch. Expected {expected_overall_count}, got {summary_data['overall_mcp_count']}"

    # Verify individual tier counts based on the mock data
    expected_tier_counts = {tier: 0 for tier in RISK_TIERS}
    for row in _MOCK_MCP_RISK_REGISTER_DATA:
        tier = row.get("risk_tier")
        if tier in expected_tier_counts:
            expected_tier_counts[tier] += 1
            
    for tier in RISK_TIERS:
        assert summary_data[tier] == expected_tier_counts[tier], \
            f"FAIL: Count for '{tier}' mismatch. Expected {expected_tier_counts[tier]}, got {summary_data[tier]}"

    print("PASS")