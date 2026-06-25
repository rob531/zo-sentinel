from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import httpx
from fastapi.testclient import TestClient

router = APIRouter()

class DecisionCreate(BaseModel):
    mcp_name: str
    decision_maker_id: int
    decision_status: str
    decision_text: str

class DecisionUpdate(BaseModel):
    decision_status: Optional[str] = None
    decision_text: Optional[str] = None

class DecisionResponse(BaseModel):
    id: int
    mcp_name: str
    decision_maker_id: int
    decision_status: str
    decision_text: str

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

async def get_write_client():
    async with httpx.AsyncClient() as client:
        yield client

@router.post("/decisions/", response_model=DecisionResponse)
async def create_decision(decision: DecisionCreate, client: httpx.AsyncClient = Depends(get_write_client)):
    response = await client.post(f"{WRITE_SERVICE_URL}/decisions/", json=decision.dict())
    if response.status_code != 201:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()

@router.get("/decisions/", response_model=List[DecisionResponse])
async def read_decisions(
    mcp_name: Optional[str] = Query(None),
    decision_maker_id: Optional[int] = Query(None),
    decision_status: Optional[str] = Query(None),
    client: httpx.AsyncClient = Depends(get_write_client)
):
    params = {}
    if mcp_name:
        params["mcp_name"] = mcp_name
    if decision_maker_id:
        params["decision_maker_id"] = decision_maker_id
    if decision_status:
        params["decision_status"] = decision_status

    response = await client.get(f"{WRITE_SERVICE_URL}/decisions/", params=params)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()

@router.get("/decisions/{decision_id}", response_model=DecisionResponse)
async def read_decision(decision_id: int, client: httpx.AsyncClient = Depends(get_write_client)):
    response = await client.get(f"{WRITE_SERVICE_URL}/decisions/{decision_id}")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()

@router.put("/decisions/{decision_id}", response_model=DecisionResponse)
async def update_decision(
    decision_id: int,
    decision: DecisionUpdate,
    client: httpx.AsyncClient = Depends(get_write_client)
):
    response = await client.put(f"{WRITE_SERVICE_URL}/decisions/{decision_id}", json=decision.dict(exclude_unset=True))
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()

@router.delete("/decisions/{decision_id}", response_model=dict)
async def delete_decision(decision_id: int, client: httpx.AsyncClient = Depends(get_write_client)):
    response = await client.delete(f"{WRITE_SERVICE_URL}/decisions/{decision_id}")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test data
    test_decision = {
        "mcp_name": "Test MCP",
        "decision_maker_id": 1,
        "decision_status": "pending",
        "decision_text": "Test decision text"
    }

    # Test create
    response = client.post("/decisions/", json=test_decision)
    assert response.status_code == 201
    decision_id = response.json()["id"]

    # Test read single
    response = client.get(f"/decisions/{decision_id}")
    assert response.status_code == 200
    assert response.json()["mcp_name"] == "Test MCP"

    # Test read all with filter
    response = client.get("/decisions/", params={"mcp_name": "Test MCP"})
    assert response.status_code == 200
    assert len(response.json()) >= 1

    # Test update
    update_data = {"decision_status": "approved"}
    response = client.put(f"/decisions/{decision_id}", json=update_data)
    assert response.status_code == 200
    assert response.json()["decision_status"] == "approved"

    # Test delete
    response = client.delete(f"/decisions/{decision_id}")
    assert response.status_code == 200

    # Verify deletion
    response = client.get(f"/decisions/{decision_id}")
    assert response.status_code == 404

    print("PASS")