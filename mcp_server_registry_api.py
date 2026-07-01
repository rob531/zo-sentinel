from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from fastapi.testclient import TestClient

# Mock database connection and model for demonstration
# In a real implementation, you would use SQLAlchemy or similar
class MCPServerRegistry(BaseModel):
    server_id: str
    hostname: str
    port: int
    trust_score: float
    last_updated: str

# Mock database
mock_db = [
    MCPServerRegistry(
        server_id="server1",
        hostname="server1.example.com",
        port=8080,
        trust_score=0.95,
        last_updated="2023-01-01T00:00:00Z"
    ),
    MCPServerRegistry(
        server_id="server2",
        hostname="server2.example.com",
        port=8081,
        trust_score=0.85,
        last_updated="2023-01-02T00:00:00Z"
    ),
]

router = APIRouter()

@router.get("/mcp/server_registry", response_model=List[MCPServerRegistry])
async def list_server_registry(
    server_id: Optional[str] = Query(None),
    min_trust_score: Optional[float] = Query(None, ge=0, le=1)
):
    """
    List MCP server registry entries with optional filtering.

    Args:
        server_id: Filter by server ID
        min_trust_score: Filter by minimum trust score (0-1)

    Returns:
        List of MCPServerRegistry records matching the filters
    """
    result = mock_db

    if server_id:
        result = [r for r in result if r.server_id == server_id]

    if min_trust_score is not None:
        result = [r for r in result if r.trust_score >= min_trust_score]

    return result

if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test 1: Get all servers
    response = client.get("/mcp/server_registry")
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Test 2: Filter by server_id
    response = client.get("/mcp/server_registry?server_id=server1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "server1"

    # Test 3: Filter by trust score
    response = client.get("/mcp/server_registry?min_trust_score=0.9")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["trust_score"] == 0.95

    # Test 4: Filter by both parameters
    response = client.get("/mcp/server_registry?server_id=server2&min_trust_score=0.8")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "server2"

    print("All tests passed!")