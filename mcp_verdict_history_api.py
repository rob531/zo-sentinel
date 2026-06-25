from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict
import requests
import json

app = FastAPI()

class VerdictHistory(BaseModel):
    mcp_name: str
    verdict_id: str
    verdict_type: str
    computed_at: str
    overall_risk: float
    criteria_version: str

@app.get("/mcp/{mcp_id}/verdict_history", response_model=List[VerdictHistory])
def get_mcp_verdict_history(mcp_id: str) -> List[Dict]:
    query = """
    SELECT
        msr.mcp_name,
        mrr.risk_register_id AS verdict_id,
        mrr.risk_tier AS verdict_type,
        mrr.computed_at,
        mrr.overall_risk,
        mrr.criteria_version
    FROM
        mcp_risk_register mrr
    JOIN
        mcp_server_registry msr ON mrr.mcp_id = msr.mcp_id
    WHERE
        mrr.mcp_id = ?
    ORDER BY
        mrr.computed_at ASC
    """

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "parameters": [mcp_id]}
    )

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Database query failed")

    result = response.json()
    if not result:
        raise HTTPException(status_code=404, detail="No verdict history found for the given MCP ID")

    return result

if __name__ == '__main__':
    from fastapi.testclient import TestClient

    # Mock the write_service query endpoint
    def mock_query_endpoint(request):
        data = request.json()
        query = data["query"]
        parameters = data["parameters"]

        if "mcp_server_registry" in query and "mcp_risk_register" in query:
            test_mcp_id = parameters[0]
            if test_mcp_id == "test_mcp_id":
                return {
                    "status_code": 200,
                    "json": lambda: [
                        {
                            "mcp_name": "Test MCP",
                            "verdict_id": "verdict_1",
                            "verdict_type": "Low",
                            "computed_at": "2023-01-01T00:00:00",
                            "overall_risk": 0.1,
                            "criteria_version": "1.0"
                        },
                        {
                            "mcp_name": "Test MCP",
                            "verdict_id": "verdict_2",
                            "verdict_type": "Medium",
                            "computed_at": "2023-01-02T00:00:00",
                            "overall_risk": 0.5,
                            "criteria_version": "1.0"
                        }
                    ]
                }
        return {"status_code": 404, "json": lambda: []}

    # Set up the test client
    client = TestClient(app)

    # Test the API
    response = client.get("/mcp/test_mcp_id/verdict_history")
    assert response.status_code == 200

    verdict_history = response.json()
    assert isinstance(verdict_history, list)
    assert len(verdict_history) > 0

    for verdict in verdict_history:
        assert isinstance(verdict, dict)
        assert "mcp_name" in verdict
        assert "verdict_id" in verdict
        assert "verdict_type" in verdict
        assert "computed_at" in verdict
        assert "overall_risk" in verdict
        assert "criteria_version" in verdict

    # Check if the verdicts are ordered chronologically
    computed_at_list = [verdict["computed_at"] for verdict in verdict_history]
    assert computed_at_list == sorted(computed_at_list)

    print("PASS")