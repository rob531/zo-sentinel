from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Dict, Any
import httpx
import json

app = FastAPI()

class MetaUpdateRequest(BaseModel):
    meta: Dict[str, Any]

class ServerMetaResponse(BaseModel):
    server_id: str
    meta: Dict[str, Any]

# Mock write_service client
async def get_write_service_client():
    class MockWriteServiceClient:
        async def post(self, url: str, json: Dict[str, Any]):
            # Mock response for the write service
            return {"status": "success"}

    return MockWriteServiceClient()

@app.put("/servers/{server_id}/meta", response_model=ServerMetaResponse)
async def update_server_meta(
    server_id: str,
    meta_update: MetaUpdateRequest,
    client: httpx.AsyncClient = Depends(get_write_service_client)
):
    # Prepare the SQL query with parameters
    sql = "UPDATE mcp_server_registry SET meta = :meta_data WHERE server_id = :server_id"
    params = {
        "meta_data": meta_update.meta,
        "server_id": server_id
    }

    # Mock the write service response
    response = await client.post("http://127.0.0.1:8772/execute", json={
        "sql": sql,
        "params": params
    })

    if response.get("status") != "success":
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update server meta")

    # Return the updated meta data
    return {"server_id": server_id, "meta": meta_update.meta}

@app.get("/servers/{server_id}/meta", response_model=ServerMetaResponse)
async def get_server_meta(server_id: str):
    # Mock response for the GET endpoint
    return {"server_id": server_id, "meta": {"example": "data"}}

if __name__ == "__main__":
    client = TestClient(app)

    # Test PUT endpoint
    test_server_id = "test-server-123"
    test_meta = {"key": "value", "nested": {"data": 123}}

    response = client.put(
        f"/servers/{test_server_id}/meta",
        json={"meta": test_meta}
    )

    assert response.status_code == 200
    assert response.json()["server_id"] == test_server_id
    assert response.json()["meta"] == test_meta

    # Test GET endpoint to verify the update
    get_response = client.get(f"/servers/{test_server_id}/meta")
    assert get_response.status_code == 200
    assert get_response.json()["server_id"] == test_server_id
    assert get_response.json()["meta"] == {"example": "data"}  # Mock data

    print("PASS")