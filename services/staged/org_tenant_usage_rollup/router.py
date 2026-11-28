"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import org_tenant_usage_rollup

router = APIRouter(prefix="/api/org", tags=["org_tenant_usage_rollup"])


class OrgRollup(BaseModel):
    org_id: int = Field(..., alias="org_id")
    name: str
    created_at: datetime
    user_count: int
    users_by_role: Dict[str, int]
    api_key_count: int
    oldest_user_at: Optional[datetime] = None
    newest_user_at: Optional[datetime] = None


class OrgTenantUsageRollupResponse(BaseModel):
    orgs: List[OrgRollup]
    total_orgs: int
    total_users: int
    total_api_keys: int


@router.get(
    "/tenant-usage-rollup",
    response_model=OrgTenantUsageRollupResponse,
    name="org_tenant_usage_rollup",
)
def get_tenant_usage_rollup(
    include_empty: bool = Query(False, description="Include orgs with no users or keys"),
    db: Session = Depends(get_session),
) -> OrgTenantUsageRollupResponse:
    """
    Return a roll‑up of usage statistics per tenant (org) and aggregate totals.
    """
    data = org_tenant_usage_rollup(db, include_empty=include_empty)
    return OrgTenantUsageRollupResponse(**data)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Import the declarative base to create tables in the in‑memory DB
    from app.db import Base  # type: ignore

    # ------------------------------------------------------------------- #
    # Setup in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    from app.models import Org, User, ApiKey  # type: ignore

    now = datetime.utcnow()
    with TestSessionLocal() as sess:
        # Orgs
        org1 = Org(id=1, name="Org One", created_at=now)
        org2 = Org(id=2, name="Org Two", created_at=now)
        sess.add_all([org1, org2])

        # Users (required fields: email, password_hash, etc.)
        user1 = User(
            id=1,
            org_id=1,
            role="admin",
            created_at=now,
            email="admin1@example.com",
            password_hash="hash1",
        )
        user2 = User(
            id=2,
            org_id=1,
            role="member",
            created_at=now,
            email="member1@example.com",
            password_hash="hash2",
        )
        user3 = User(
            id=3,
            org_id=2,
            role="admin",
            created_at=now,
            email="admin2@example.com",
            password_hash="hash3",
        )
        sess.add_all([user1, user2, user3])

        # API Keys
        key1 = ApiKey(
            id=1,
            org_id=1,
            label="key1",
            key_hash="khash1",
            created_at=now,
        )
        key2 = ApiKey(
            id=2,
            org_id=2,
            label="key2",
            key_hash="khash2",
            created_at=now,
        )
        sess.add_all([key1, key2])

        sess.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with overridden dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/api/org/tenant-usage-rollup")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = resp.json()
    try:
        assert payload["total_orgs"] == 2
        assert payload["total_users"] == 3
        first_org = payload["orgs"][0]
        assert first_org["users_by_role"] == {"admin": 1, "member": 1}
    except AssertionError:
        print("FAIL: contract assertions failed", file=sys.stderr)
        sys.exit(1)

    print("PASS")