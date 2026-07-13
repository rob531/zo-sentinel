# perspective_admin_crud_api.py
# FastAPI router for CRUD operations on perspectives and perspective_snapshots tables

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime
import requests
from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/perspectives", tags=["perspectives"])

class PerspectiveCreate(BaseModel):
    name: str
    description: Optional[str] = Field(default=None)
    facet_filters: dict
    org_id: str
    created_by: str

class PerspectiveUpdate(BaseModel):
    name: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    facet_filters: Optional[dict] = Field(default=None)

class PerspectiveResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = Field(default=None)
    facet_filters: dict
    org_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime

class PerspectiveSnapshotResponse(BaseModel):
    id: int
    perspective_id: UUID
    name: str
    taken_at: datetime
    membership: dict

def _write_to_service(table: str, data: dict) -> dict:
    response = requests.post(
        "http://127.0.0.1:8772/write",
        json={"table": table, "data": data}
    )
    response.raise_for_status()
    return response.json()

def _query_service(sql: str, params: dict = None) -> List[dict]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}}
    )
    response.raise_for_status()
    return response.json()

@router.post("/", response_model=PerspectiveResponse, status_code=status.HTTP_201_CREATED)
async def create_perspective(perspective: PerspectiveCreate):
    data = perspective.dict()
    data["id"] = str(uuid4())
    result = _write_to_service("perspectives", data)
    return PerspectiveResponse(**result)

@router.get("/", response_model=List[PerspectiveResponse])
async def list_perspectives(org_id: str):
    sql = "SELECT * FROM perspectives WHERE org_id = :org_id"
    results = _query_service(sql, {"org_id": org_id})
    return [PerspectiveResponse(**row) for row in results]

@router.get("/{id}", response_model=PerspectiveResponse)
async def get_perspective(id: UUID):
    sql = "SELECT * FROM perspectives WHERE id = :id"
    results = _query_service(sql, {"id": str(id)})
    if not results:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return PerspectiveResponse(**results[0])

@router.patch("/{id}", response_model=PerspectiveResponse)
async def update_perspective(id: UUID, perspective: PerspectiveUpdate):
    data = perspective.dict(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")

    data["id"] = str(id)
    result = _write_to_service("perspectives", data)
    return PerspectiveResponse(**result)

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_perspective(id: UUID):
    _write_to_service("perspectives", {"id": str(id)})
    return

@router.post("/{id}/snapshots", response_model=PerspectiveSnapshotResponse, status_code=status.HTTP_201_CREATED)
async def create_snapshot(id: UUID, name: str):
    # Get latest snapshot membership
    sql = """
        SELECT membership FROM perspective_snapshots
        WHERE perspective_id = :perspective_id
        ORDER BY taken_at DESC
        LIMIT 1
    """
    results = _query_service(sql, {"perspective_id": str(id)})
    membership = results[0]["membership"] if results else {}

    data = {
        "perspective_id": str(id),
        "name": name,
        "membership": membership
    }
    result = _write_to_service("perspective_snapshots", data)
    return PerspectiveSnapshotResponse(**result)

@router.get("/{id}/snapshots", response_model=List[PerspectiveSnapshotResponse])
async def list_snapshots(id: UUID):
    sql = "SELECT * FROM perspective_snapshots WHERE perspective_id = :perspective_id"
    results = _query_service(sql, {"perspective_id": str(id)})
    return [PerspectiveSnapshotResponse(**row) for row in results]

@router.get("/{id}/membership/{snapshot_id}", response_model=dict)
async def get_membership(id: UUID, snapshot_id: int):
    sql = """
        SELECT membership FROM perspective_snapshots
        WHERE id = :id AND perspective_id = :perspective_id
    """
    results = _query_service(sql, {"id": snapshot_id, "perspective_id": str(id)})
    if not results:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return results[0]["membership"]

if __name__ == "__main__":
    import requests_mock
    from fastapi.testclient import TestClient
    from app.main import app

    # Mock write_service responses
    with requests_mock.Mocker() as m:
        # Mock perspective creation
        m.post("http://127.0.0.1:8772/write", json=lambda r: {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "name": r.json()["data"]["name"],
            "description": r.json()["data"]["description"],
            "facet_filters": r.json()["data"]["facet_filters"],
            "org_id": r.json()["data"]["org_id"],
            "created_by": r.json()["data"]["created_by"],
            "created_at": "2023-01-01T00:00:00",
            "updated_at": "2023-01-01T00:00:00"
        })

        # Mock perspective query
        m.post("http://127.0.0.1:8772/query", json=lambda r: [
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "Test Perspective",
                "description": "Test Description",
                "facet_filters": {},
                "org_id": "test-org",
                "created_by": "test-user",
                "created_at": "2023-01-01T00:00:00",
                "updated_at": "2023-01-01T00:00:00"
            }
        ] if r.json()["sql"].startswith("SELECT * FROM perspectives") else [])

        # Mock snapshot creation
        m.post("http://127.0.0.1:8772/write", json=lambda r: {
            "id": 1,
            "perspective_id": r.json()["data"]["perspective_id"],
            "name": r.json()["data"]["name"],
            "taken_at": "2023-01-01T00:00:00",
            "membership": r.json()["data"]["membership"]
        } if r.json()["table"] == "perspective_snapshots" else {})

        # Mock snapshot query
        m.post("http://127.0.0.1:8772/query", json=lambda r: [
            {
                "id": 1,
                "perspective_id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "Test Snapshot",
                "taken_at": "2023-01-01T00:00:00",
                "membership": {}
            }
        ] if r.json()["sql"].startswith("SELECT * FROM perspective_snapshots") else [])

        client = TestClient(app)

        # Test perspective creation
        response = client.post("/", json={
            "name": "Test Perspective",
            "description": "Test Description",
            "facet_filters": {},
            "org_id": "test-org",
            "created_by": "test-user"
        })
        assert response.status_code == 201
        assert "id" in response.json()

        # Test perspective retrieval
        response = client.get(f"/{response.json()['id']}")
        assert response.status_code == 200
        assert response.json()["name"] == "Test Perspective"

        # Test perspective deletion
        response = client.delete(f"/{response.json()['id']}")
        assert response.status_code == 204

        # Test snapshot creation
        response = client.post(f"/{response.json()['id']}/snapshots", json={"name": "Test Snapshot"})
        assert response.status_code == 201
        assert "id" in response.json()

        print("PASS")