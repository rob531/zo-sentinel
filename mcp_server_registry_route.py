# mcp_server_registry_route.py
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from . import mcp_server_registry_query, mcp_server_registry_serializer

router = APIRouter()

class ServerRegistryRecord(BaseModel):
    server_id: str
    trust_score: float
    # Add other fields as needed

@router.get("/mcp/server_registry", response_model=List[ServerRegistryRecord])
async def get_server_registry(
    server_id: Optional[str] = Query(None),
    min_trust_score: Optional[float] = Query(None),
    db_session=Depends(mcp_server_registry_query.get_db_session)
):
    rows = mcp_server_registry_query.query_server_registry(db_session, server_id, min_trust_score)
    return [mcp_server_registry_serializer.serialize(row) for row in rows]

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Override the DB session dependency with a stub
    def get_db_session_stub():
        return [
            {"server_id": "server1", "trust_score": 0.9},
            {"server_id": "server2", "trust_score": 0.8},
            {"server_id": "server3", "trust_score": 0.7},
        ]

    app.dependency_overrides[mcp_server_registry_query.get_db_session] = get_db_session_stub

    client = TestClient(app)

    # Test GET /mcp/server_registry with no parameters
    response = client.get("/mcp/server_registry")
    assert response.status_code == 200
    assert response.json() == [
        {"server_id": "server1", "trust_score": 0.9},
        {"server_id": "server2", "trust_score": 0.8},
        {"server_id": "server3", "trust_score": 0.7},
    ]

    # Test GET /mcp/server_registry with server_id parameter
    response = client.get("/mcp/server_registry?server_id=server1")
    assert response.status_code == 200
    assert response.json() == [{"server_id": "server1", "trust_score": 0.9}]

    # Test GET /mcp/server_registry with min_trust_score parameter
    response = client.get("/mcp/server_registry?min_trust_score=0.8")
    assert response.status_code == 200
    assert response.json() == [
        {"server_id": "server1", "trust_score": 0.9},
        {"server_id": "server2", "trust_score": 0.8},
    ]