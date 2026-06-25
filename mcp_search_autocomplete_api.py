from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
import requests

router = APIRouter()

class MCPNameResponse(BaseModel):
    names: List[str]

@router.get("/mcp/autocomplete", response_model=MCPNameResponse)
async def autocomplete_mcp(query: str):
    # Parameterized query to prevent SQL injection
    sql_query = """
        SELECT mcp_name
        FROM mcp_server_registry
        WHERE mcp_name LIKE %s
        LIMIT 10
    """
    params = (f"%{query}%",)

    try:
        # Query the internal write_service
        response = requests.post(
            "http://write_service/execute_sql",
            json={"query": sql_query, "params": params}
        )
        response.raise_for_status()
        results = response.json()

        # Extract MCP names from the results
        mcp_names = [row["mcp_name"] for row in results]
        return {"names": mcp_names}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

# Test block
if __name__ == "__main__":
    from fastapi import FastAPI

    # Mock in-memory store for testing
    test_data = [
        {"mcp_name": "test1"},
        {"mcp_name": "test2"},
        {"mcp_name": "example"},
        {"mcp_name": "testing"},
        {"mcp_name": "prod1"},
        {"mcp_name": "prod2"},
    ]

    # Mock write_service response
    def mock_post(url, json):
        if "test" in json["params"][0]:
            return requests.Response()
        return requests.Response()

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Override requests.post for testing
    requests.post = mock_post

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/mcp/autocomplete?query=test")
    assert response.status_code == 200
    assert len(response.json()["names"]) <= 10
    assert all("test" in name.lower() for name in response.json()["names"])

    print("PASS")