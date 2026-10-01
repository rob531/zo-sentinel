# deps: fastapi, sqlalchemy, requests
"""Perspective Management API.

CRUD endpoints for Perspective resources, scoped by org_id.
Uses the shared app DB session (`get_session`) and SQLAlchemy models
imported from `app.models`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import Perspective

router = APIRouter(prefix="/api", tags=["perspective_management_api"])


# --- Pydantic request/response models ---------------------------------------

class PerspectiveCreate(BaseModel):
    name: str
    description: str = ""
    facet_filters: Optional[dict] = None
    created_by: str = "system"
    org_id: Optional[str] = None


class PerspectiveUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    facet_filters: Optional[dict] = None


class PerspectiveResponse(BaseModel):
    id: str
    org_id: Optional[str]
    name: str
    description: str
    facet_filters: dict
    created_by: str
    created_at: Optional[str]
    updated_at: Optional[str]

    class Config:
        from_attributes = True


# --- Endpoints ------------------------------------------------------------

@router.get("/perspectives", response_model=List[PerspectiveResponse])
def list_perspectives(
    org_id: Optional[str] = None,
    db: Session = Depends(get_session),
):
    """List perspectives, optionally filtered by org_id."""
    q = db.query(Perspective)
    if org_id:
        q = q.filter(Perspective.org_id == org_id)
    return q.all()


@router.post(
    "/perspectives",
    response_model=PerspectiveResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_perspective(
    payload: PerspectiveCreate,
    db: Session = Depends(get_session),
):
    """Create a new perspective."""
    db_obj = Perspective(
        name=payload.name,
        description=payload.description,
        facet_filters=payload.facet_filters or {},
        created_by=payload.created_by,
        org_id=payload.org_id,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@router.get("/perspectives/{perspective_id}", response_model=PerspectiveResponse)
def get_perspective(
    perspective_id: str,
    db: Session = Depends(get_session),
):
    """Get a perspective by id."""
    obj = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return obj


@router.put("/perspectives/{perspective_id}", response_model=PerspectiveResponse)
def update_perspective(
    perspective_id: str,
    payload: PerspectiveUpdate,
    db: Session = Depends(get_session),
):
    """Update a perspective."""
    obj = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Perspective not found")
    if payload.name is not None:
        obj.name = payload.name
    if payload.description is not None:
        obj.description = payload.description
    if payload.facet_filters is not None:
        obj.facet_filters = payload.facet_filters
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/perspectives/{perspective_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_perspective(
    perspective_id: str,
    db: Session = Depends(get_session),
):
    """Delete a perspective."""
    obj = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Perspective not found")
    db.delete(obj)
    db.commit()


# --- Self-test ------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from contextlib import contextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def override_get_session():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # POST /api/perspectives  (create)
    resp = client.post(
        "/api/perspectives",
        json={
            "name": "Test Perspective",
            "description": "A test perspective",
            "facet_filters": {"risk_tier": ["HIGH"]},
            "created_by": "test_user",
            "org_id": "org-test-001",
        },
    )
    if resp.status_code != 201:
        print(f"FAIL: create returned {resp.status_code} {resp.text}")
        sys.exit(1)
    data = resp.json()
    pid = data["id"]
    if data["name"] != "Test Perspective":
        print(f"FAIL: create name mismatch {data}")
        sys.exit(1)

    # GET /api/perspectives  (list all)
    resp = client.get("/api/perspectives")
    if resp.status_code != 200:
        print(f"FAIL: list returned {resp.status_code}")
        sys.exit(1)
    if len(resp.json()) != 1:
        print(f"FAIL: expected 1 row, got {len(resp.json())}")
        sys.exit(1)

    # GET /api/perspectives/{id}  (get one)
    resp = client.get(f"/api/perspectives/{pid}")
    if resp.status_code != 200:
        print(f"FAIL: get returned {resp.status_code}")
        sys.exit(1)
    if resp.json()["name"] != "Test Perspective":
        print(f"FAIL: get name mismatch")
        sys.exit(1)

    # PUT /api/perspectives/{id}  (update)
    resp = client.put(f"/api/perspectives/{pid}", json={"name": "Updated Perspective"})
    if resp.status_code != 200:
        print(f"FAIL: update returned {resp.status_code}")
        sys.exit(1)
    if resp.json()["name"] != "Updated Perspective":
        print(f"FAIL: update name mismatch")
        sys.exit(1)

    # DELETE /api/perspectives/{id}  (delete)
    resp = client.delete(f"/api/perspectives/{pid}")
    if resp.status_code != 204:
        print(f"FAIL: delete returned {resp.status_code}")
        sys.exit(1)

    # GET /api/perspectives/{id}  (404 after delete)
    resp = client.get(f"/api/perspectives/{pid}")
    if resp.status_code != 404:
        print(f"FAIL: expected 404 after delete, got {resp.status_code}")
        sys.exit(1)

    print("PASS")
