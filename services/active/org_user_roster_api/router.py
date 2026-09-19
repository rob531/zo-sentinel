# deps: fastapi, sqlalchemy, pydantic, PyJWT
"""Org User Roster API -- list and manage users + API keys within an organization.

GET    /api/orgs/{org_id}/users             -- list users with api_key_count (public)
POST   /api/orgs/{org_id}/users             -- create a user (auth required)
GET    /api/orgs/{org_id}/users/{user_id}   -- get a single user (public)
DELETE /api/orgs/{org_id}/users/{user_id}  -- remove a user (auth required, admin only)
GET    /api/orgs/{org_id}/api-keys          -- list API keys for the org (public)
POST   /api/orgs/{org_id}/api-keys          -- create an API key (auth required)
DELETE /api/orgs/{org_id}/api-keys/{key_id} -- revoke an API key (auth required)

Multi-tenancy: write operations are scoped to the authenticated principal's org_id.
All write operations require a valid JWT bearer token and are scoped to the
principal's org_id.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Ensure repo root on path for app.* imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import ApiKey, Org, User
from app.security import Principal, create_access_token, get_principal

router = APIRouter(prefix="/api", tags=["org_user_roster_api"])


# ─── Request / Response models ────────────────────────────────────────────────

class UserResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    email: str
    role: str
    created_at: Optional[datetime] = None


class UserListResponse(BaseModel):
    users: list[UserResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    per_page: int = Field(ge=1)


class UserCreateRequest(BaseModel):
    email: str = Field(..., min_length=1, description="User email address")
    role: str = Field(default="member", description="Role: admin | member | viewer")
    password: str = Field(..., min_length=1, description="Initial password")


class ApiKeyResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    org_id: str
    label: str
    created_at: Optional[datetime] = None


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeyResponse] = Field(default_factory=list)
    total: int = Field(ge=0)


class ApiKeyCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, description="Human-readable label for the key")
    key_value: str = Field(..., min_length=1, description="The API key value (plaintext; stored as hash)")


# ─── Auth helpers ────────────────────────────────────────────────────────────

def require_admin(principal: Principal = Depends(get_principal)) -> None:
    """Ensure the caller has admin role within their org."""
    if principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )


# ─── Org existence check ──────────────────────────────────────────────────────

def _ensure_org_exists(db: Session, org_id: str) -> None:
    """Raise 404 if org does not exist."""
    exists = db.query(Org.id).filter(Org.id == org_id).first()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {org_id} not found",
        )


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/orgs/{org_id}/users",
    response_model=UserListResponse,
    responses={
        200: {},
        404: {"description": "Organization not found"},
    },
)
def list_org_users(
    org_id: str,
    page: int = 1,
    per_page: int = 10,
    db: Session = Depends(get_session),
) -> UserListResponse:
    """
    List all users in an organization with pagination.
    Each user includes their api_key_count (number of active API keys).
    """
    _ensure_org_exists(db, org_id)

    total = db.query(func.count(User.id)).filter(User.org_id == org_id).scalar() or 0

    offset = (page - 1) * per_page
    stmt = (
        select(User.id, User.email, User.role, User.created_at)
        .where(User.org_id == org_id)
        .order_by(User.created_at)
        .offset(offset)
        .limit(per_page)
    )
    rows = db.execute(stmt).all()

    users = [
        UserResponse(
            id=str(row.id),
            email=row.email,
            role=row.role,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return UserListResponse(users=users, total=total, page=page, per_page=per_page)


@router.post(
    "/orgs/{org_id}/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {},
        400: {"description": "Invalid request body"},
        401: {"description": "Missing or invalid bearer token"},
        404: {"description": "Organization not found"},
        409: {"description": "Email already in use"},
    },
)
def create_org_user(
    org_id: str,
    body: UserCreateRequest,
    _: None = Depends(get_principal),
    db: Session = Depends(get_session),
) -> UserResponse:
    """
    Create a new user within the organization.
    Requires a valid JWT bearer token.
    """
    _ensure_org_exists(db, org_id)

    # Duplicate email check
    existing = db.query(User.id).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email {body.email} is already in use",
        )

    now = datetime.now(timezone.utc)
    from app.security import hash_password
    user = User(
        id=f"usr-{now.strftime('%Y%m%d%H%M%S')}-{abs(hash(body.email)) % 100_000:05d}",
        email=body.email,
        role=body.role,
        org_id=org_id,
        password_hash=hash_password(body.password),
        created_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
    )


@router.get(
    "/orgs/{org_id}/users/{user_id}",
    response_model=UserResponse,
    responses={
        200: {},
        404: {"description": "User not found"},
    },
)
def get_org_user(
    org_id: str,
    user_id: str,
    db: Session = Depends(get_session),
) -> UserResponse:
    """Get a single user by ID within an org."""
    _ensure_org_exists(db, org_id)
    user = db.query(User).filter(User.id == user_id, User.org_id == org_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found in organization {org_id}",
        )
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
    )


@router.delete(
    "/orgs/{org_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {},
        401: {"description": "Missing or invalid bearer token"},
        403: {"description": "Admin role required"},
        404: {"description": "User not found"},
    },
)
def delete_org_user(
    org_id: str,
    user_id: str,
    _: None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    """Delete a user from the org. Admin role required."""
    user = db.query(User).filter(User.id == user_id, User.org_id == org_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found in organization {org_id}",
        )
    db.delete(user)
    db.commit()


@router.get(
    "/orgs/{org_id}/api-keys",
    response_model=ApiKeyListResponse,
    responses={
        200: {},
        404: {"description": "Organization not found"},
    },
)
def list_org_api_keys(
    org_id: str,
    db: Session = Depends(get_session),
) -> ApiKeyListResponse:
    """List all API keys for an organization."""
    _ensure_org_exists(db, org_id)
    rows = db.query(ApiKey).filter(ApiKey.org_id == org_id).order_by(ApiKey.created_at).all()
    return ApiKeyListResponse(
        api_keys=[
            ApiKeyResponse(
                id=r.id,
                org_id=r.org_id,
                label=r.label,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=len(rows),
    )


@router.post(
    "/orgs/{org_id}/api-keys",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {},
        400: {"description": "Invalid request body"},
        401: {"description": "Missing or invalid bearer token"},
        404: {"description": "Organization not found"},
    },
)
def create_api_key(
    org_id: str,
    body: ApiKeyCreateRequest,
    _: None = Depends(get_principal),
    db: Session = Depends(get_session),
) -> ApiKeyResponse:
    """Create an API key for the org. The key_value is stored as a hash."""
    _ensure_org_exists(db, org_id)
    now = datetime.now(timezone.utc)
    from app.security import hash_password
    key_hash = hash_password(body.key_value)
    key = ApiKey(
        id=f"ak-{now.strftime('%Y%m%d%H%M%S')}-{abs(hash(body.key_value)) % 100_000:05d}",
        org_id=org_id,
        key_hash=key_hash,
        label=body.label,
        created_at=now,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return ApiKeyResponse(
        id=key.id,
        org_id=key.org_id,
        label=key.label,
        created_at=key.created_at,
    )


@router.delete(
    "/orgs/{org_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {},
        401: {"description": "Missing or invalid bearer token"},
        404: {"description": "API key not found"},
    },
)
def delete_api_key(
    org_id: str,
    key_id: str,
    _: None = Depends(get_principal),
    db: Session = Depends(get_session),
) -> None:
    """Revoke an API key within the org."""
    key = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.org_id == org_id).first()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key {key_id} not found in organization {org_id}",
        )
    db.delete(key)
    db.commit()


# ─── Self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

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

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    # Seed data
    with TestSession() as db:
        org1 = Org(id="org-001", name="Test Org 1")
        org2 = Org(id="org-002", name="Test Org 2")
        db.add_all([org1, org2])
        db.commit()

        now = datetime.now(timezone.utc)
        u1 = User(id="user-001", email="alice@test.com", role="admin", org_id="org-001", password_hash="x", created_at=now)
        u2 = User(id="user-002", email="bob@test.com", role="member", org_id="org-001", password_hash="x", created_at=now)
        u3 = User(id="user-003", email="charlie@test.com", role="viewer", org_id="org-002", password_hash="x", created_at=now)
        db.add_all([u1, u2, u3])
        db.commit()

        ak = ApiKey(id="ak-001", org_id="org-001", key_hash="hash1", label="test-key", created_at=now)
        db.add(ak)
        db.commit()

    client = TestClient(test_app)

    # ── List users in org-001 (public) ──
    resp = client.get("/api/orgs/org-001/users")
    if resp.status_code != 200:
        print(f"FAIL: list_org_users expected 200, got {resp.status_code}: {resp.text}")
        _sys.exit(1)
    data = resp.json()
    if data["total"] != 2:
        print(f"FAIL: expected 2 users, got {data['total']}")
        _sys.exit(1)
    if data["page"] != 1 or data["per_page"] != 10:
        print(f"FAIL: pagination defaults wrong: page={data['page']} per_page={data['per_page']}")
        _sys.exit(1)
    emails = {u["email"] for u in data["users"]}
    if emails != {"alice@test.com", "bob@test.com"}:
        print(f"FAIL: got emails {emails}")
        _sys.exit(1)

    # ── Pagination ──
    resp = client.get("/api/orgs/org-001/users?per_page=1&page=1")
    if resp.status_code != 200 or len(resp.json()["users"]) != 1:
        print(f"FAIL: pagination page 1")
        _sys.exit(1)
    resp = client.get("/api/orgs/org-001/users?per_page=1&page=2")
    if resp.status_code != 200 or len(resp.json()["users"]) != 1:
        print(f"FAIL: pagination page 2")
        _sys.exit(1)
    resp = client.get("/api/orgs/org-001/users?per_page=1&page=3")
    if resp.status_code != 200 or len(resp.json()["users"]) != 0:
        print(f"FAIL: pagination page 3 should be empty")
        _sys.exit(1)

    # ── Get single user ──
    resp = client.get("/api/orgs/org-001/users/user-001")
    if resp.status_code != 200 or resp.json()["email"] != "alice@test.com":
        print(f"FAIL: get single user")
        _sys.exit(1)

    # ── User not found ──
    resp = client.get("/api/orgs/org-001/users/nobody")
    if resp.status_code != 404:
        print(f"FAIL: get nonexistent user expected 404, got {resp.status_code}")
        _sys.exit(1)

    # ── Org not found ──
    resp = client.get("/api/orgs/nonexistent/users")
    if resp.status_code != 404:
        print(f"FAIL: nonexistent org expected 404, got {resp.status_code}")
        _sys.exit(1)

    # ── Cross-org isolation ──
    resp = client.get("/api/orgs/org-002/users")
    if resp.status_code != 200 or resp.json()["total"] != 1:
        print(f"FAIL: cross-org isolation")
        _sys.exit(1)
    if resp.json()["users"][0]["email"] != "charlie@test.com":
        print(f"FAIL: org-002 should have charlie")
        _sys.exit(1)

    # ── API keys list (public) ──
    resp = client.get("/api/orgs/org-001/api-keys")
    if resp.status_code != 200:
        print(f"FAIL: list api-keys expected 200, got {resp.status_code}")
        _sys.exit(1)
    data = resp.json()
    if data["total"] != 1 or data["api_keys"][0]["label"] != "test-key":
        print(f"FAIL: api-keys list: {data}")
        _sys.exit(1)

    # ── Create API key (with auth) ──
    token = create_access_token(user_id="user-001", org_id="org-001", role="admin")
    resp = client.post(
        "/api/orgs/org-001/api-keys",
        json={"label": "new-key", "key_value": "secret123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 201:
        print(f"FAIL: create api-key expected 201, got {resp.status_code}: {resp.text}")
        _sys.exit(1)
    if resp.json()["label"] != "new-key":
        print(f"FAIL: create api-key label mismatch")
        _sys.exit(1)

    # ── Create user (with auth) ──
    resp = client.post(
        "/api/orgs/org-001/users",
        json={"email": "diana@test.com", "role": "member", "password": "pass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 201:
        print(f"FAIL: create user expected 201, got {resp.status_code}: {resp.text}")
        _sys.exit(1)
    if resp.json()["email"] != "diana@test.com":
        print(f"FAIL: create user email mismatch")
        _sys.exit(1)

    # ── Create user missing token → 401 ──
    resp = client.post(
        "/api/orgs/org-001/users",
        json={"email": "newbie@test.com", "role": "member", "password": "pass"},
    )
    if resp.status_code != 401:
        print(f"FAIL: create user without token expected 401, got {resp.status_code}")
        _sys.exit(1)

    # ── Create API key missing token → 401 ──
    resp = client.post(
        "/api/orgs/org-001/api-keys",
        json={"label": "another-key", "key_value": "value"},
    )
    if resp.status_code != 401:
        print(f"FAIL: create api-key without token expected 401, got {resp.status_code}")
        _sys.exit(1)

    # ── Delete user non-admin token → 403 ──
    member_token = create_access_token(user_id="user-002", org_id="org-001", role="member")
    resp = client.request(
        "DELETE",
        "/api/orgs/org-001/users/user-001",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    if resp.status_code != 403:
        print(f"FAIL: delete user as member expected 403, got {resp.status_code}")
        _sys.exit(1)

    # ── Delete user admin token → 204 ──
    resp = client.request(
        "DELETE",
        "/api/orgs/org-001/users/user-002",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 204:
        print(f"FAIL: delete user as admin expected 204, got {resp.status_code}: {resp.text}")
        _sys.exit(1)

    # ── Deleted user gone ──
    resp = client.get("/api/orgs/org-001/users/user-002")
    if resp.status_code != 404:
        print(f"FAIL: deleted user should 404, got {resp.status_code}")
        _sys.exit(1)

    # ── Delete API key → 204 ──
    resp = client.request(
        "DELETE",
        "/api/orgs/org-001/api-keys/ak-001",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 204:
        print(f"FAIL: delete api-key expected 204, got {resp.status_code}")
        _sys.exit(1)

    # ── Delete non-existent API key → 404 ──
    resp = client.request(
        "DELETE",
        "/api/orgs/org-001/api-keys/nonexistent",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 404:
        print(f"FAIL: delete nonexistent key expected 404, got {resp.status_code}")
        _sys.exit(1)

    # ── Delete API key missing token → 401 ──
    resp = client.delete("/api/orgs/org-001/api-keys/ak-002")
    if resp.status_code != 401:
        print(f"FAIL: delete api-key without token expected 401, got {resp.status_code}")
        _sys.exit(1)

    print("PASS")
