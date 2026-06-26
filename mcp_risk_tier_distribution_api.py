from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from typing import Dict
import uvicorn
from fastapi.testclient import TestClient

app = FastAPI()

class RiskTierDistribution(BaseModel):
    risk_tier_distribution: Dict[str, int]

WRITE_SERVICE_URL = "http://write_service/execute_sql"

@app.get("/mcp_risk_tier_distribution_summary", response_model=RiskTierDistribution)
async def get_risk_tier_distribution():
    sql_query = """
    SELECT risk_tier, COUNT(*) as count
    FROM mcp_risk_register
    GROUP BY risk_tier
    """

    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"query": sql_query}
        )
        response.raise_for_status()
        data = response.json()

        # Initialize all possible risk tiers with 0 count
        risk_tiers = {
            "TRUSTED_GENERAL": 0,
            "TRUSTED_RESEARCH": 0,
            "CONTROLLED": 0,
            "RESTRICTED": 0,
            "UNKNOWN": 0
        }

        # Update counts from query results
        for row in data:
            tier = row["risk_tier"]
            count = row["count"]
            if tier in risk_tiers:
                risk_tiers[tier] = count

        return {"risk_tier_distribution": risk_tiers}

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Mock in-memory store for testing
    class MockWriteService:
        def __init__(self):
            self.data = [
                {"risk_tier": "TRUSTED_GENERAL", "count": 5},
                {"risk_tier": "TRUSTED_RESEARCH", "count": 3},
                {"risk_tier": "CONTROLLED", "count": 2},
                {"risk_tier": "RESTRICTED", "count": 1}
            ]

        def post(self, url, json):
            if url == WRITE_SERVICE_URL:
                return self.data
            return []

    # Replace requests.post with our mock
    original_post = requests.post
    requests.post = MockWriteService().post

    client = TestClient(app)

    response = client.get("/mcp_risk_tier_distribution_summary")
    assert response.status_code == 200
    result = response.json()

    expected_tiers = {
        "TRUSTED_GENERAL": 5,
        "TRUSTED_RESEARCH": 3,
        "CONTROLLED": 2,
        "RESTRICTED": 1,
        "UNKNOWN": 0
    }

    assert result["risk_tier_distribution"] == expected_tiers
    print("PASS")

    # Restore original requests.post
    requests.post = original_post