# deps: fastapi, sqlalchemy, requests
"""Perspective Management Service Router.

Provides CRUD endpoints for perspective management using the shared app DB session
via ``get_session`` and SQLAlchemy models imported from ``app.models``.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import Perspective

router = APIRouter(prefix="/api", tags=["perspective_management"])


class PerspectiveCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    facet_filters: Optional[dict] = None
    created_by: Optional[str] = "system"
    org_id: Optional[str] = None


class PerspectiveUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    facet_filters: Optional[dict] = None


class PerspectiveResponse(BaseModel):
    id: str
    org_id: Optional[str] = None
    name: str
    description: str
    facet_filters: dict
    created_by: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/perspectives", response_model=List[PerspectiveResponse])
def list_perspectives(
    org_id: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """List all perspectives, optionally filtered by org_id."""
    query = db.query(Perspective)
    if org_id:
        query = query.filter(Perspective.org_id == org_id)
    return query.all()


@router.post("/perspectives", response_model=PerspectiveResponse, status_code=status.HTTP_201_CREATED)
def create_perspective(
    perspective: PerspectiveCreate,
    db: Session = Depends(get_session)
):
    """Create a new perspective."""
    db_perspective = Perspective(
        name=perspective.name,
        description=perspective.description,
        facet_filters=perspective.facet_filters or {},
        created_by=perspective.created_by,
        org_id=perspective.org_id,
    )
    db.add(db_perspective)
    db.commit()
    db.refresh(db_perspective)
    return db_perspective


@router.get("/perspectives/{perspective_id}", response_model=PerspectiveResponse)
def get_perspective(
    perspective_id: str,
    db: Session = Depends(get_session)
):
    """Get a perspective by ID."""
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return perspective


@router.put("/perspectives/{perspective_id}", response_model=PerspectiveResponse)
def update_perspective(
    perspective_id: str,
    perspective: PerspectiveUpdate,
    db: Session = Depends(get_session)
):
    """Update a perspective."""
    db_perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not db_perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    if perspective.name is not None:
        db_perspective.name = perspective.name
    if perspective.description is not None:
        db_perspective.description = perspective.description
    if perspective.facet_filters is not None:
        db_perspective.facet_filters = perspective.facet_filters

    db.commit()
    db.refresh(db_perspective)
    return db_perspective


@router.delete("/perspectives/{perspective_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_perspective(
    perspective_id: str,
    db: Session = Depends(get_session)
):
    """Delete a perspective."""
    db_perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not db_perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    db.delete(db_perspective)
    db.commit()


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from app.main import app
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_perspective = {
        "name": "Test Perspective",
        "description": "A test perspective",
        "facet_filters": {"risk_tier": ["HIGH"]},
        "created_by": "test_user"
    }

    # Test POST
    response = client.post("/api/perspectives", json=test_perspective)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == test_perspective["name"]
    perspective_id = data["id"]

    # Test GET list
    response = client.get("/api/perspectives")
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Test GET one
    response = client.get(f"/api/perspectives/{perspective_id}")
    assert response.status_code == 200
    assert response.json()["name"] == test_perspective["name"]

    # Test PUT
    updated = {"name": "Updated Perspective"}
    response = client.put(f"/api/perspectives/{perspective_id}", json=updated)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Perspective"

    # Test DELETE
    response = client.delete(f"/api/perspectives/{perspective_id}")
    assert response.status_code == 204

    # Test GET after delete
    response = client.get(f"/api/perspectives/{perspective_id}")
    assert response.status_code == 404

    print("PASS")
