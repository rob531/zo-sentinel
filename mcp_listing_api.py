from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
import requests
from typing import List, Optional

app = FastAPI()

class MCP(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    endpoint: str
    # Add other non-sensitive fields as needed

class MCPListResponse(BaseModel):
    mcps: List[MCP]
    total_count: int
    page: int
    page_size: int

@app.get("/mcp/list", response_model=MCPListResponse)
async def get_mcp_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    offset = (page - 1) * page_size

    # Query the write_service for total count
    count_query = """
    SELECT COUNT(*) FROM mcp_server_registry
    """
    count_response = requests.get(
        "http://127.0.0.1:8772/query",
        params={"query": count_query}
    )
    if count_response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to query MCP count")

    total_count = count_response.json()["data"][0]["count"]

    # Query the write_service for paginated results
    query = f"""
    SELECT id, name, description, endpoint FROM mcp_server_registry
    LIMIT {page_size} OFFSET {offset}
    """
    response = requests.get(
        "http://127.0.0.1:8772/query",
        params={"query": query}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to query MCPs")

    mcps = [MCP(**row) for row in response.json()["data"]]

    return {
        "mcps": mcps,
        "total_count": total_count,
        "page": page,
        "page_size": page_size
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Mock the write_service responses for testing
    def mock_get(*args, **kwargs):
        if "COUNT" in kwargs["params"]["query"]:
            return {"data": [{"count": 50}]}
        elif "LIMIT 20 OFFSET 0" in kwargs["params"]["query"]:
            return {"data": [{"id": 1, "name": "MCP1", "endpoint": "http://mcp1"}, {"id": 2, "name": "MCP2", "endpoint": "http://mcp2"}]}
        elif "LIMIT 10 OFFSET 0" in kwargs["params"]["query"]:
            return {"data": [{"id": 1, "name": "MCP1", "endpoint": "http://mcp1"}]}
        elif "LIMIT 20 OFFSET 20" in kwargs["params"]["query"]:
            return {"data": [{"id": 3, "name": "MCP3", "endpoint": "http://mcp3"}, {"id": 4, "name": "MCP4", "endpoint": "http://mcp4"}]}
        else:
            return {"data": []}

    requests.get = mock_get

    client = TestClient(app)

    # Test default pagination
    response = client.get("/mcp/list")
    assert response.status_code == 200
    assert response.json()["total_count"] == 50
    assert len(response.json()["mcps"]) == 2
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20

    # Test custom pagination
    response = client.get("/mcp/list?page=2&page_size=20")
    assert response.status_code == 200
    assert response.json()["total_count"] == 50
    assert len(response.json()["mcps"]) == 2
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 20

    # Test smaller page size
    response = client.get("/mcp/list?page_size=10")
    assert response.status_code == 200
    assert response.json()["total_count"] == 50
    assert len(response.json()["mcps"]) == 1
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 10

    print("All tests passed!")