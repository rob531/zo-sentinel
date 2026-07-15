from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx
from app.db import get_session
from app.models import Perspective, ServerRegistry, Organization
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter(tags=["perspectives"])

class PerspectiveCreate(BaseModel):
    org_id: str
    name: str
    description: str
    created_by: str
    facet_filters: Optional[Dict[str, Any]] = None

class PerspectiveUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    facet_filters: Optional[Dict[str, Any]] = None

class PerspectiveSummary(BaseModel):
    id: int
    org_id: str
    name: str
    description: str
    server_count: int
    created_at: datetime

class ServerDetail(BaseModel):
    server_id: str
    name: str
    risk_tier: str

class PerspectiveDetail(PerspectiveSummary):
    facet_filters: Optional[Dict[str, Any]] = None
    created_by: str
    updated_at: datetime
    servers: List[ServerDetail]

async def get_write_service_client():
    async with httpx.AsyncClient() as client:
        yield client

@router.get("/", response_model=List[PerspectiveSummary])
async def list_perspectives(db: Session = Depends(get_session)):
    perspectives = db.query(
        Perspective.id,
        Perspective.org_id,
        Perspective.name,
        Perspective.description,
        Perspective.created_at,
        func.count(ServerRegistry.id).label("server_count")
    ).join(
        ServerRegistry, Perspective.id == ServerRegistry.perspective_id
    ).group_by(
        Perspective.id
    ).all()

    return [
        PerspectiveSummary(
            id=perspective.id,
            org_id=perspective.org_id,
            name=perspective.name,
            description=perspective.description,
            server_count=perspective.server_count,
            created_at=perspective.created_at
        ) for perspective in perspectives
    ]

@router.get("/{perspective_id}", response_model=PerspectiveDetail)
async def get_perspective(
    perspective_id: int,
    db: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_write_service_client)
):
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    servers = db.query(
        ServerRegistry.id,
        ServerRegistry.name,
        ServerRegistry.risk_tier
    ).filter(
        ServerRegistry.perspective_id == perspective_id
    ).all()

    response = await client.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": f"SELECT * FROM mcp_server_registry WHERE perspective_id = {perspective_id}"
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Error fetching server data")

    server_details = response.json()

    return PerspectiveDetail(
        id=perspective.id,
        org_id=perspective.org_id,
        name=perspective.name,
        description=perspective.description,
        server_count=len(servers),
        created_at=perspective.created_at,
        facet_filters=perspective.facet_filters,
        created_by=perspective.created_by,
        updated_at=perspective.updated_at,
        servers=[
            ServerDetail(
                server_id=server.id,
                name=server.name,
                risk_tier=server.risk_tier
            ) for server in servers
        ]
    )

@router.post("/", response_model=PerspectiveDetail, status_code=status.HTTP_201_CREATED)
async def create_perspective(
    perspective: PerspectiveCreate,
    db: Session = Depends(get_session)
):
    org = db.query(Organization).filter(Organization.id == perspective.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    new_perspective = Perspective(
        org_id=perspective.org_id,
        name=perspective.name,
        description=perspective.description,
        created_by=perspective.created_by,
        facet_filters=perspective.facet_filters
    )
    db.add(new_perspective)
    db.commit()
    db.refresh(new_perspective)

    return PerspectiveDetail(
        id=new_perspective.id,
        org_id=new_perspective.org_id,
        name=new_perspective.name,
        description=new_perspective.description,
        server_count=0,
        created_at=new_perspective.created_at,
        facet_filters=new_perspective.facet_filters,
        created_by=new_perspective.created_by,
        updated_at=new_perspective.updated_at,
        servers=[]
    )

@router.put("/{perspective_id}", response_model=PerspectiveDetail)
async def update_perspective(
    perspective_id: int,
    perspective: PerspectiveUpdate,
    db: Session = Depends(get_session)
):
    existing_perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not existing_perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    if perspective.name is not None:
        existing_perspective.name = perspective.name
    if perspective.description is not None:
        existing_perspective.description = perspective.description
    if perspective.facet_filters is not None:
        existing_perspective.facet_filters = perspective.facet_filters

    db.commit()
    db.refresh(existing_perspective)

    servers = db.query(
        ServerRegistry.id,
        ServerRegistry.name,
        ServerRegistry.risk_tier
    ).filter(
        ServerRegistry.perspective_id == perspective_id
    ).all()

    return PerspectiveDetail(
        id=existing_perspective.id,
        org_id=existing_perspective.org_id,
        name=existing_perspective.name,
        description=existing_perspective.description,
        server_count=len(servers),
        created_at=existing_perspective.created_at,
        facet_filters=existing_perspective.facet_filters,
        created_by=existing_perspective.created_by,
        updated_at=existing_perspective.updated_at,
        servers=[
            ServerDetail(
                server_id=server.id,
                name=server.name,
                risk_tier=server.risk_tier
            ) for server in servers
        ]
    )

@router.delete("/{perspective_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_perspective(
    perspective_id: int,
    db: Session = Depends(get_session)
):
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    db.delete(perspective)
    db.commit()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_org = Organization(id="test-org", name="Test Org")
    test_session = TestSession()
    test_session.add(test_org)
    test_session.commit()

    client = TestClient(app)

    # Test 1: POST /perspectives
    response = client.post(
        "/perspectives",
        json={
            "org_id": "test-org",
            "name": "Test Perspective",
            "description": "A test perspective",
            "created_by": "test-user",
            "facet_filters": {}
        }
    )
    assert response.status_code == 200
    perspective_id = response.json()["id"]

    # Test 2: GET /perspectives
    response = client.get("/perspectives")
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert any(p["name"] == "Test Perspective" for p in response.json())

    # Test 3: GET /perspectives/{id}
    response = client.get(f"/perspectives/{perspective_id}")
    assert response.status_code == 200
    assert response.json()["server_count"] == 0
    assert isinstance(response.json()["servers"], list)

    # Test 4: DELETE /perspectives/{id}
    response = client.delete(f"/perspectives/{perspective_id}")
    assert response.status_code == 204

    # Test 5: Verify response shapes
    response = client.get("/perspectives")
    assert all(
        set(p.keys()) == {"id", "org_id", "name", "description", "server_count", "created_at"}
        for p in response.json()
    )

    print("PASS")