from fastapi import FastAPI, APIRouter, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI()
router = APIRouter()

class ServerRegistry(BaseModel):
    server_id: str
    trust_score: float
    # Add other fields as needed

@router.get("/mcp/server_registry", response_model=List[ServerRegistry])
async def list_server_registry(
    server_id: Optional[str] = Query(None, description="Filter by server ID"),
    trust_score: Optional[float] = Query(None, description="Filter by trust score")
):
    # In a real application, you would query the database here
    # For this example, we'll return a mock list of servers
    mock_servers = [
        {"server_id": "server1", "trust_score": 0.9},
        {"server_id": "server2", "trust_score": 0.7},
        {"server_id": "server3", "trust_score": 0.8},
    ]

    # Apply filters if provided
    filtered_servers = mock_servers
    if server_id:
        filtered_servers = [s for s in filtered_servers if s["server_id"] == server_id]
    if trust_score is not None:
        filtered_servers = [s for s in filtered_servers if s["trust_score"] == trust_score]

    return filtered_servers

app.include_router(router)

if __name__ == "__main__":
    client = TestClient(app)

    # Test without filters
    response = client.get("/mcp/server_registry")
    assert response.status_code == 200
    assert len(response.json()) == 3

    # Test with server_id filter
    response = client.get("/mcp/server_registry?server_id=server1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "server1"

    # Test with trust_score filter
    response = client.get("/mcp/server_registry?trust_score=0.7")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["trust_score"] == 0.7

    # Test with both filters
    response = client.get("/mcp/server_registry?server_id=server1&trust_score=0.9")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "server1"
    assert response.json()[0]["trust_score"] == 0.9

    print("All tests passed!")