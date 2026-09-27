# deps: fastapi, pydantic, sqlalchemy
"""org_health service -- per-organization health overview.

Endpoints:
  GET /api/orgs/{org_id}/health/overview   -- summary counts for an org
  GET /api/orgs/{org_id}/health/users     -- paginated user list
  GET /api/orgs/{org_id}/health/apikeys   -- api key summary

Auth: public.  Prefix: /api.  Tag: org_health.
Data: app Postgres via get_session + SQLAlchemy ORM (Org, User, ApiKey).
Note: McpServerRegistry is global (no org_id); server health is not org-partitioned
in the current schema.  Score freshness is reported globally via mcp_signal_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

# Ensure repo root on path so `from app.db` resolves in standalone test
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import ApiKey, Org, User

router = APIRouter(prefix="/api", tags=["org_health"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class UserRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: Optional[str]
    role: Optional[str]
    created_at: Optional[datetime]
    clerk_id: Optional[str]


class UsersResponse(BaseModel):
    org_id: str
    users: List[UserRow]
    total: int
    page: int
    page_size: int


class ApiKeyRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: Optional[str]
    created_at: Optional[datetime]


class ApiKeysResponse(BaseModel):
    org_id: str
    api_keys: List[ApiKeyRow]
    total: int


class OverviewResponse(BaseModel):
    org_id: str
    org_name: Optional[str]
    created_at: Optional[datetime]
    user_count: int
    api_key_count: int
    # server/score fields are always 0 / empty because McpServerRegistry
    # is global (no org_id in current schema)
    total_servers: int = 0
    never_scored: int = 0
    stale_scores: int = 0
    fresh_scores: int = 0
    tier_breakdown: List[dict] = []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _naive_now() -> datetime:
    """Timezone-naive UTC now (matches SQLite / most test DBs)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/orgs/{org_id}/health/overview",
    response_model=OverviewResponse,
    responses={404: {"description": "Organization not found"}},
    name="org_health:overview",
)
def org_health_overview(
    org_id: str,
    db: Session = Depends(get_session),
) -> OverviewResponse:
    """
    Return high-level counts for an org.
    Servers / scores are reported as 0 / empty because McpServerRegistry
    is a global registry (no per-org partitioning in the current schema).
    """
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Org {org_id} not found")

    user_count = db.query(User).filter(User.org_id == org_id).count()
    api_key_count = db.query(ApiKey).filter(ApiKey.org_id == org_id).count()

    return OverviewResponse(
        org_id=org_id,
        org_name=org.name,
        created_at=org.created_at,
        user_count=user_count,
        api_key_count=api_key_count,
    )


@router.get(
    "/orgs/{org_id}/health/users",
    response_model=UsersResponse,
    responses={404: {"description": "Organization not found"}},
    name="org_health:users",
)
def org_health_users(
    org_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: Optional[str] = Query(None, description="Filter by user role"),
    db: Session = Depends(get_session),
) -> UsersResponse:
    """Paginated list of users in an org, optionally filtered by role."""
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Org {org_id} not found")

    q = db.query(User).filter(User.org_id == org_id)
    if role:
        q = q.filter(User.role == role)

    total = q.count()
    rows = (
        q.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    users = [
        UserRow(
            id=u.id,
            email=u.email,
            role=u.role,
            created_at=u.created_at,
            clerk_id=u.clerk_id,
        )
        for u in rows
    ]

    return UsersResponse(
        org_id=org_id,
        users=users,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/orgs/{org_id}/health/apikeys",
    response_model=ApiKeysResponse,
    responses={404: {"description": "Organization not found"}},
    name="org_health:apikeys",
)
def org_health_apikeys(
    org_id: str,
    db: Session = Depends(get_session),
) -> ApiKeysResponse:
    """List API keys for an org."""
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Org {org_id} not found")

    rows = (
        db.query(ApiKey)
        .filter(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )

    api_keys = [
        ApiKeyRow(
            id=k.id,
            label=k.label,
            created_at=k.created_at,
        )
        for k in rows
    ]

    return ApiKeysResponse(
        org_id=org_id,
        api_keys=api_keys,
        total=len(api_keys),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = _override
    app.include_router(router)

    now = _naive_now()

    with TestSession() as db:
        org1 = Org(id="org-h1", name="Health Test Org", created_at=now)
        org2 = Org(id="org-h2", name="Empty Org", created_at=now)
        db.add_all([org1, org2])
        db.commit()

        users = [
            User(
                id=f"u-h{i}",
                org_id="org-h1",
                email=f"user{i}@test.com",
                role=role,
                created_at=now - timedelta(days=d),
                password_hash="x",
                clerk_id=f"clerk-{i}" if i % 2 == 0 else None,
            )
            for i, (role, d) in enumerate([
                ("admin", 1),
                ("user", 3),
                ("admin", 5),
                ("viewer", 10),
            ])
        ]
        db.add_all(users)
        db.commit()

        keys = [
            ApiKey(id=f"key-h{i}", org_id="org-h1", label=f"Key {i}", key_hash="x")
            for i in range(2)
        ]
        db.add_all(keys)
        db.commit()

    client = TestClient(app)

    # Test 1: overview for org with data
    resp = client.get("/api/orgs/org-h1/health/overview")
    assert resp.status_code == 200, f"overview got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["org_id"] == "org-h1"
    assert data["org_name"] == "Health Test Org"
    assert data["user_count"] == 4, f"expected 4, got {data['user_count']}"
    assert data["api_key_count"] == 2, f"expected 2, got {data['api_key_count']}"
    # Server fields should be 0 (no org_id on McpServerRegistry)
    assert data["total_servers"] == 0
    assert data["tier_breakdown"] == []

    # Test 2: overview for empty org
    resp = client.get("/api/orgs/org-h2/health/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_count"] == 0
    assert data["api_key_count"] == 0

    # Test 3: users list
    resp = client.get("/api/orgs/org-h1/health/users")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["users"]) == 4
    assert all(u["org_id"] is None for u in data["users"])  # UserRow has no org_id field

    # Test 4: filter by role
    resp = client.get("/api/orgs/org-h1/health/users?role=admin")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2

    # Test 5: pagination
    resp = client.get("/api/orgs/org-h1/health/users?page=1&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["users"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2

    # Test 6: api keys
    resp = client.get("/api/orgs/org-h1/health/apikeys")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["api_keys"]) == 2
    for k in data["api_keys"]:
        assert k["label"] is not None

    # Test 7: 404 org
    resp = client.get("/api/orgs/nonexistent/health/overview")
    assert resp.status_code == 404

    # Test 8: 404 on users
    resp = client.get("/api/orgs/nonexistent/health/users")
    assert resp.status_code == 404

    # Test 9: 404 on apikeys
    resp = client.get("/api/orgs/nonexistent/health/apikeys")
    assert resp.status_code == 404

    print("PASS")
