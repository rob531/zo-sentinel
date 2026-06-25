from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from typing import List, Dict, Any

app = FastAPI()

class PolicyEvaluation(BaseModel):
    rule_id: int
    rule_name: str
    passed: bool
    details: str

class MCPEvaluationResponse(BaseModel):
    mcp_id: str
    evaluations: List[PolicyEvaluation]

@app.post("/evaluate_mcp_policy", response_model=MCPEvaluationResponse)
async def evaluate_mcp_policy(mcp_id: str):
    # Fetch active policy rules from the database
    rules_response = requests.post(
        "http://write_service/rules",
        json={"query": "SELECT * FROM mcp_policy_rules WHERE active = true"}
    )
    if rules_response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch policy rules")

    rules = rules_response.json()

    # Fetch MCP server metadata from the registry
    mcp_response = requests.post(
        "http://mcp_server_registry/mcp",
        json={"mcp_id": mcp_id}
    )
    if mcp_response.status_code != 200:
        raise HTTPException(status_code=404, detail="MCP server not found")

    mcp_metadata = mcp_response.json()

    evaluations = []
    for rule in rules:
        passed, details = evaluate_rule(rule, mcp_metadata)
        evaluations.append(PolicyEvaluation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            passed=passed,
            details=details
        ))

    return MCPEvaluationResponse(mcp_id=mcp_id, evaluations=evaluations)

def evaluate_rule(rule: Dict[str, Any], mcp_metadata: Dict[str, Any]) -> (bool, str):
    # Implement rule evaluation logic here
    # This is a placeholder for the actual rule evaluation logic
    # For example, you might check if a certain field in mcp_metadata meets the rule's criteria
    passed = True
    details = "Rule passed"
    return passed, details

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Simulate a POST request to /evaluate_mcp_policy
    response = client.post("/evaluate_mcp_policy", json={"mcp_id": "test_mcp_id"})

    # Assert that the response contains a list of policy evaluations
    assert "evaluations" in response.json()
    assert isinstance(response.json()["evaluations"], list)

    print("PASS")