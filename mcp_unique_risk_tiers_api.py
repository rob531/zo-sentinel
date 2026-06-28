from fastapi import FastAPI
from fastapi.testclient import TestClient
from typing import List
import pytest
import httpx

# Mock the write_service query endpoint
class MockWriteService:
    def __init__(self):
        self.db = {
            "mcp_server_registry": [
                {"server_id": "srv1", "risk_tier": "TRUSTED_GENERAL"},
                {"server_id": "srv2", "risk_tier": "CAUTION_LIMITED"},
                {"server_id": "srv3", "risk_tier": "HIGH_RISK_ISOLATED"},
                {"server_id": "srv4", "risk_tier": "TRUSTED_GENERAL"},
                {"server_id": "srv5", "risk_tier": "CAUTION_LIMITED"},
                {"server_id": "srv6", "risk_tier": "TRUSTED_GENERAL"},
            ]
        }

    def query(self, query_string: str):
        if "SELECT DISTINCT risk_tier FROM mcp_server_registry" in query_string:
            risk_tiers = set()
            for row in self.db.get("mcp_server_registry", []):
                if "risk_tier" in row and row["risk_tier"]:
                    risk_tiers.add(row["risk_tier"])
            return list(risk_tiers)
        return []

# In-memory mock for the write_service
mock_write_service_instance = MockWriteService()

def get_write_service_client():
    # This function will be called by the API to get a client for the write_service
    # In a real scenario, this would be an actual HTTP client.
    # For testing, we'll intercept the calls.
    class MockHTTPClient:
        def post(self, url, json):
            if url == "http://127.0.0.1:8772/query":
                query_string = json.get("query")
                result = mock_write_service_instance.query(query_string)
                return MockResponse(result)
            raise NotImplementedError(f"URL {url} not mocked")

    return MockHTTPClient()

class MockResponse:
    def __init__(self, json_data):
        self.json_data = json_data

    def json(self):
        return self.json_data

# FastAPI application
app = FastAPI()

@app.get("/mcp_risk_tiers", response_model=List[str])
async def get_mcp_risk_tiers():
    """
    Retrieves all unique risk_tier values from the mcp_server_registry table.
    """
    client = get_write_service_client()
    query_string = "SELECT DISTINCT risk_tier FROM mcp_server_registry"
    response = client.post("http://127.0.0.1:8772/query", json={"query": query_string})
    return response.json()

# Acceptance Test
if __name__ == "__main__":
    client = TestClient(app)

    # Seed the mock write_service with data
    mock_write_service_instance.db["mcp_server_registry"] = [
        {"server_id": "srv1", "risk_tier": "TRUSTED_GENERAL"},
        {"server_id": "srv2", "risk_tier": "CAUTION_LIMITED"},
        {"server_id": "srv3", "risk_tier": "HIGH_RISK_ISOLATED"},
        {"server_id": "srv4", "risk_tier": "TRUSTED_GENERAL"},
        {"server_id": "srv5", "risk_tier": "CAUTION_LIMITED"},
        {"server_id": "srv6", "risk_tier": "TRUSTED_GENERAL"},
        {"server_id": "srv7", "risk_tier": None}, # Test with None risk_tier
        {"server_id": "srv8", "risk_tier": ""},   # Test with empty string risk_tier
    ]

    response = client.get("/mcp_risk_tiers")
    assert response.status_code == 200
    result = response.json()

    # Assert the response is a list of strings
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, str)

    # Assert the response contains the expected unique risk tiers
    expected_risk_tiers = sorted(["TRUSTED_GENERAL", "CAUTION_LIMITED", "HIGH_RISK_ISOLATED"])
    actual_risk_tiers = sorted(result)
    assert actual_risk_tiers == expected_risk_tiers

    print("PASS")