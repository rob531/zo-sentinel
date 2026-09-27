# deps: fastapi, sqlalchemy, pydantic
"""Organization Entity Search API -- search users within an organization.

GET /api/orgs/{org_id}/entities/search
  Returns paginated list of users for the given org, with optional email/role filter.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on users table.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

# Ensure repo root on path for app.* imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import Org, User

router = APIRouter(prefix="/api", tags=["org_entity_search_api"])


# --- Response models --------------------------------------------------------

class EntityResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str = Field(..., description="User ID")
    email: str = Field(..., description="User email address")
    role: str = Field(..., description="User role within the org")


class SearchResponse(BaseModel):
    entities: List[EntityResponse] = Field(default_factory=list, description="List of matching users")
    total: int = Field(..., ge=0, description="Total count of matching users")
    page: int = Field(..., ge=1, description="Current page number")
    per_page: int = Field(..., ge=1, description="Items per page")


# --- Endpoint ---------------------------------------------------------------

@router.get(
    "/orgs/{org_id}/entities/search",
    response_model=SearchResponse,
    responses={
        404: {"description": "Organization not found"},
    },
)
def search_org_entities(
    org_id: str,
    query: Optional[str] = Query(None, description="Search filter on email or role"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Search users within an organization with optional text filter and pagination."""
    # Verify org exists
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {org_id} not found",
        )

    # Build base query
    base_query = db.query(User).filter(User.org_id == org_id)

    # Apply text filter if provided
    if query:
        search_filter = f"%{query}%"
        base_query = base_query.filter(
            or_(
                User.email.ilike(search_filter),
                User.role.ilike(search_filter),
            )
        )

    # Get total count
    total = base_query.count()

    # Apply pagination
    offset = (page - 1) * per_page
    users = base_query.offset(offset).limit(per_page).all()

    # Build response
    entities = [
        EntityResponse(id=u.id, email=u.email, role=u.role)
        for u in users
    ]

    return SearchResponse(
        entities=entities,
        total=total,
        page=page,
        per_page=per_page,
    )


# --- Self-test --------------------------------------------------------------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # Create in-memory SQLite engine with StaticPool for test isolation
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    def _override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Create test app with router
    test_app = FastAPI()
    test_app.include_router(router)

    # Override dependency for testing
    test_app.dependency_overrides[get_session] = _override_get_session

    # Seed test data
    with TestSession() as db:
        org1 = Org(id="org-001", name="Test Org 1")
        org2 = Org(id="org-002", name="Test Org 2")
        db.add_all([org1, org2])
        db.commit()

        users = [
            User(id="user-001", email="alice@test.com", role="admin", org_id="org-001", password_hash="x"),
            User(id="user-002", email="bob@test.com", role="member", org_id="org-001", password_hash="x"),
            User(id="user-003", email="charlie@test.com", role="viewer", org_id="org-001", password_hash="x"),
            User(id="user-004", email="diana@test.com", role="admin", org_id="org-002", password_hash="x"),
            User(id="user-005", email="eve@test.com", role="member", org_id="org-002", password_hash="x"),
        ]
        db.add_all(users)
        db.commit()

    client = TestClient(test_app)

    # Test 1: List all users in org-001
    resp = client.get("/api/orgs/org-001/entities/search")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["total"] == 3, f"Expected 3 users, got {data['total']}"
    assert len(data["entities"]) == 3
    assert data["page"] == 1

    # Test 2: Filter by query (email)
    resp = client.get("/api/orgs/org-001/entities/search?query=alice")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["entities"][0]["email"] == "alice@test.com"

    # Test 3: Filter by role
    resp = client.get("/api/orgs/org-001/entities/search?query=admin")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["entities"][0]["role"] == "admin"

    # Test 4: Pagination
    resp = client.get("/api/orgs/org-001/entities/search?per_page=2&page=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entities"]) == 2
    assert data["total"] == 3

    resp = client.get("/api/orgs/org-001/entities/search?per_page=2&page=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entities"]) == 1
    assert data["total"] == 3

    # Test 5: Org not found
    resp = client.get("/api/orgs/nonexistent/entities/search")
    assert resp.status_code == 404

    # Test 6: Cross-org isolation (org-002 should not see org-001 users)
    resp = client.get("/api/orgs/org-002/entities/search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(e["email"].endswith("@test.com") for e in data["entities"])

    print("PASS")
