from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel
from typing import List, Optional
import httpx
import json
from fastapi.testclient import TestClient

app = FastAPI()

# Pydantic models
class MCPExemptionCreate(BaseModel):
    mcp_name: str
    exemption_id: str
    status: str
    description: Optional[str] = None

class MCPExemption(MCPExemptionCreate):
    id: int

# Dependency for write_service client
async def get_write_service_client():
    async with httpx.AsyncClient() as client:
        yield client

# CRUD Endpoints
@app.post("/exemptions", response_model=MCPExemption, status_code=status.HTTP_201_CREATED)
async def create_exemption(exemption: MCPExemptionCreate, client: httpx.AsyncClient = Depends(get_write_service_client)):
    query = """
    INSERT INTO mcp_exemptions (mcp_name, exemption_id, status, description)
    VALUES (%s, %s, %s, %s)
    RETURNING id, mcp_name, exemption_id, status, description
    """
    params = (exemption.mcp_name, exemption.exemption_id, exemption.status, exemption.description)

    response = await client.post(
        "http://write_service/execute",
        json={"query": query, "params": params}
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())

    return response.json()

@app.get("/exemptions", response_model=List[MCPExemption])
async def list_exemptions(
    mcp_name: Optional[str] = None,
    exemption_id: Optional[str] = None,
    status: Optional[str] = None,
    client: httpx.AsyncClient = Depends(get_write_service_client)
):
    query = "SELECT id, mcp_name, exemption_id, status, description FROM mcp_exemptions WHERE 1=1"
    params = []

    if mcp_name:
        query += " AND mcp_name = %s"
        params.append(mcp_name)
    if exemption_id:
        query += " AND exemption_id = %s"
        params.append(exemption_id)
    if status:
        query += " AND status = %s"
        params.append(status)

    response = await client.post(
        "http://write_service/execute",
        json={"query": query, "params": params}
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())

    return response.json()

@app.get("/exemptions/{exemption_id}", response_model=MCPExemption)
async def get_exemption(
    exemption_id: str,
    client: httpx.AsyncClient = Depends(get_write_service_client)
):
    query = """
    SELECT id, mcp_name, exemption_id, status, description
    FROM mcp_exemptions
    WHERE exemption_id = %s
    """
    params = (exemption_id,)

    response = await client.post(
        "http://write_service/execute",
        json={"query": query, "params": params}
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())

    result = response.json()
    if not result:
        raise HTTPException(status_code=404, detail="Exemption not found")

    return result[0]

@app.put("/exemptions/{exemption_id}", response_model=MCPExemption)
async def update_exemption(
    exemption_id: str,
    exemption: MCPExemptionCreate,
    client: httpx.AsyncClient = Depends(get_write_service_client)
):
    query = """
    UPDATE mcp_exemptions
    SET mcp_name = %s, status = %s, description = %s
    WHERE exemption_id = %s
    RETURNING id, mcp_name, exemption_id, status, description
    """
    params = (exemption.mcp_name, exemption.status, exemption.description, exemption_id)

    response = await client.post(
        "http://write_service/execute",
        json={"query": query, "params": params}
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())

    result = response.json()
    if not result:
        raise HTTPException(status_code=404, detail="Exemption not found")

    return result[0]

@app.delete("/exemptions/{exemption_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exemption(
    exemption_id: str,
    client: httpx.AsyncClient = Depends(get_write_service_client)
):
    query = "DELETE FROM mcp_exemptions WHERE exemption_id = %s"
    params = (exemption_id,)

    response = await client.post(
        "http://write_service/execute",
        json={"query": query, "params": params}
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())

    if response.json()["rowcount"] == 0:
        raise HTTPException(status_code=404, detail="Exemption not found")

# Test block
if __name__ == "__main__":
    client = TestClient(app)

    # Test data
    test_exemption = {
        "mcp_name": "test_mcp",
        "exemption_id": "test_exempt_001",
        "status": "active",
        "description": "Test exemption"
    }

    # Test CREATE
    response = client.post("/exemptions", json=test_exemption)
    assert response.status_code == 201
    exemption_id = response.json()["exemption_id"]

    # Test READ (single)
    response = client.get(f"/exemptions/{exemption_id}")
    assert response.status_code == 200
    assert response.json()["exemption_id"] == exemption_id

    # Test READ (list)
    response = client.get("/exemptions", params={"mcp_name": "test_mcp"})
    assert response.status_code == 200
    assert len(response.json()) >= 1

    # Test UPDATE
    update_data = {
        "mcp_name": "updated_mcp",
        "exemption_id": exemption_id,
        "status": "inactive",
        "description": "Updated test exemption"
    }
    response = client.put(f"/exemptions/{exemption_id}", json=update_data)
    assert response.status_code == 200
    assert response.json()["status"] == "inactive"

    # Test DELETE
    response = client.delete(f"/exemptions/{exemption_id}")
    assert response.status_code == 204

    # Verify DELETE
    response = client.get(f"/exemptions/{exemption_id}")
    assert response.status_code == 404

    print("PASS")