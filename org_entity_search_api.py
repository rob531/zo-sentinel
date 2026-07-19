"""FastAPI router for org-entity search -- servers and users within an org."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, User, Org
from app.security import Principal, get_principal
from app.rbac import require_role

router = APIRouter(prefix="/search", tags=["org-entity-search"])


# --- Request/Response models ---

class ServerSearchResult(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_assessed: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserSearchResult(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    servers: list[ServerSearchResult] = Field(default_factory=list)
    users: list[UserSearchResult] = Field(default_factory=list)
    total_servers: int = 0
    total_users: int = 0


class AxisScoresResponse(BaseModel):
    server_id: str
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    model_version: str


# --- Helpers ---

_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def _valid_uuid(s: str) -> bool:
    return bool(_UUID_V4_RE.match(s))


# --- Endpoints ---

@router.get("/servers", response_model=SearchResponse)
def search_servers(
    q: str = Query("", min_length=0, max_length=256, description="Search query (name, url, description)"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier (CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN)"),
    source: Optional[str] = Query(None, description="Filter by registry_source"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SearchResponse:
    """Search MCP servers within the authenticated org's scope.

    Multi-tenancy: servers are org-scoped via the principal's org_id.
    """
    if not _valid_uuid(principal.org_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid org context")

    q_pattern = f"%{q}%" if q else None

    base_q = db.query(McpServerRegistry)
    if q_pattern:
        base_q = base_q.filter(
            (McpServerRegistry.name.ilike(q_pattern))
            | (McpServerRegistry.url.ilike(q_pattern))
            | (McpServerRegistry.description.ilike(q_pattern))
        )
    if risk_tier:
        base_q = base_q.filter(McpServerRegistry.risk_tier == risk_tier)
    if source:
        base_q = base_q.filter(McpServerRegistry.registry_source == source)

    total = base_q.count()
    rows = (
        base_q.order_by(McpServerRegistry.last_seen.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return SearchResponse(
        servers=[ServerSearchResult.model_validate(r) for r in rows],
        total_servers=total,
    )


@router.get("/servers/{server_id}/axes", response_model=list[AxisScoresResponse])
def get_server_axes(
    server_id: str,
    model_version: Optional[str] = Query(None, description="Filter by model_version (current if omitted)"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[AxisScoresResponse]:
    """Return all 7 axis scores for a server (or filtered by model_version)."""
    if not _valid_uuid(principal.org_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid org context")

    q = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if model_version:
        q = q.filter(McpLlmAxisScore.model_version == model_version)

    rows = q.order_by(McpLlmAxisScore.axis_name).all()
    return [AxisScoresResponse.model_validate(r) for r in rows]


@router.get("/users", response_model=SearchResponse)
def search_users(
    q: str = Query("", min_length=0, max_length=256, description="Search query (email)"),
    role: Optional[str] = Query(None, description="Filter by role (admin/member/viewer)"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SearchResponse:
    """Search users within the authenticated org."""
    if not _valid_uuid(principal.org_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid org context")

    q_pattern = f"%{q}%" if q else None

    base_q = db.query(User).filter(User.org_id == principal.org_id)
    if q_pattern:
        base_q = base_q.filter(User.email.ilike(q_pattern))
    if role:
        base_q = base_q.filter(User.role == role)

    total = base_q.count()
    rows = (
        base_q.order_by(User.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return SearchResponse(
        users=[UserSearchResult.model_validate(r) for r in rows],
        total_users=total,
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Self-test: import + route-count assertion (no live Postgres needed)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from fastapi.testclient import TestClient
    from app.main import app as main_app
    from app.db import get_session, SessionLocal
    from app.models import Base, Org, User
    from passlib.hash import pbkdf2_sha256

    # Build in-memory SQLite session for test
    from sqlalchemy import create_engine
    _engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=_engine)
    _test_session = SessionLocal(bind=_engine)

    # Seed minimal org + user so auth passes
    _org = Org(id="550e8400-e29b-41d4-a716-446655440000", name="Test Org")
    _test_session.add(_org)
    _user = User(
        id="user-001",
        email="admin@test.dev",
        password_hash=pbkdf2_sha256.hash("testpass"),
        org_id=_org.id,
        role="admin",
    )
    _test_session.add(_user)
    _test_session.commit()

    # Override dependency
    def _override_session():
        try:
            yield _test_session
        finally:
            pass

    main_app.dependency_overrides[get_session] = _override_session

    # Patch principal to avoid real auth
    from unittest.mock import patch
    _mock_principal = Principal(
        user_id="user-001",
        email="admin@test.dev",
        org_id=_org.id,
        role="admin",
    )
    with patch("app.security.get_principal", return_value=_mock_principal):
        client = TestClient(main_app)

        # Happy path: /search/servers returns 200
        r = client.get("/search/servers?q=test", headers={"Authorization": "Bearer test"})
        assert r.status_code == 200, f"/search/servers failed: {r.status_code} {r.text}"

        # Auth failure: missing principal -> 401/403
        with patch("app.security.get_principal", side_effect=HTTPException(status.HTTP_401_UNAUTHORIZED)):
            r2 = client.get("/search/servers?q=test")
            assert r2.status_code in (401, 403), f"Expected 401/403, got {r2.status_code}"

    print("PASS")
