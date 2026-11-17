"""logic.py -- data/computation for org_api_key_usage_stats service.

Provides a pure function that queries the real application models (ApiKey,
User, Org) via a SQLAlchemy Session and returns a JSON‑serialisable dict
describing API‑key usage for a given organisation.

The module can be exercised directly via the ``__main__`` block which spins
up an in‑memory SQLite DB, seeds it, mounts a minimal FastAPI app and asserts
the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import ApiKey, Org, User

# ---------------------------------------------------------------------------


def org_api_key_usage_stats(db: Session, org_id: int) -> Dict:
    """Return usage statistics for an organisation's API keys.

    The result shape is::

        {
            "org_id": int,
            "key_count": int,
            "keys": [
                {
                    "key_id": int,
                    "label": str,
                    "created_at": str,   # ISO‑8601
                    "age_days": int
                },
                ...
            ],
            "user_count": int,
            "role_breakdown": {
                "<role>": int,
                ...
            }
        }

    No secret material (e.g. ``key_hash``) is ever returned.
    """
    # -----------------------------------------------------------------------
    # Organisation existence – raise if not found (mirrors typical API behaviour)
    org_exists = db.execute(
        select(Org.id).where(Org.id == org_id)
    ).first()
    if not org_exists:
        raise ValueError(f"Organisation {org_id} not found")

    # -----------------------------------------------------------------------
    # API keys for the org
    key_rows = db.execute(
        select(ApiKey.id, ApiKey.label, ApiKey.created_at)
        .where(ApiKey.org_id == org_id)
    ).all()

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    keys: List[Dict] = []
    for key_id, label, created_at in key_rows:
        # Ensure datetime is timezone‑aware for subtraction
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (now - created_at).days
        keys.append(
            {
                "key_id": key_id,
                "label": label,
                "created_at": created_at.isoformat(),
                "age_days": age_days,
            }
        )

    # -----------------------------------------------------------------------
    # Users for the org
    user_rows = db.execute(
        select(User.role, func.count())
        .where(User.org_id == org_id)
        .group_by(User.role)
    ).all()

    role_breakdown: Dict[str, int] = {role: int(cnt) for role, cnt in user_rows}
    user_count = sum(role_breakdown.values())

    # -----------------------------------------------------------------------
    return {
        "org_id": org_id,
        "key_count": len(keys),
        "keys": keys,
        "user_count": user_count,
        "role_breakdown": role_breakdown,
    }


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Self‑test: spin up an in‑memory DB, seed data, invoke via FastAPI test client.
    from fastapi import APIRouter, Depends, FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # -----------------------------------------------------------------------
    # Create in‑memory SQLite engine compatible with the app's Base metadata.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # -----------------------------------------------------------------------
    # Override the ``get_session`` dependency to use the test engine.
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # -----------------------------------------------------------------------
    # Seed required rows: one Org, two ApiKeys, two Users.
    with SessionLocal() as db:
        org = Org(id=1, name="Acme Corp")
        db.add(org)

        key1 = ApiKey(
            id=10,
            org_id=1,
            key_hash="hash1",
            label="first-key",
            created_at=datetime.utcnow().replace(tzinfo=timezone.utc),
        )
        key2 = ApiKey(
            id=20,
            org_id=1,
            key_hash="hash2",
            label="second-key",
            created_at=datetime.utcnow().replace(tzinfo=timezone.utc),
        )
        db.add_all([key1, key2])

        user1 = User(
            id=100,
            org_id=1,
            email="alice@example.com",
            role="admin",
            password_hash="pwdhash1",
            created_at=datetime.utcnow().replace(tzinfo=timezone.utc),
        )
        user2 = User(
            id=200,
            org_id=1,
            email="bob@example.com",
            role="member",
            password_hash="pwdhash2",
            created_at=datetime.utcnow().replace(tzinfo=timezone.utc),
        )
        db.add_all([user1, user2])

        db.commit()

    # -----------------------------------------------------------------------
    # Minimal FastAPI app exposing the endpoint.
    router = APIRouter()

    @router.get(
        "/api/orgs/{org_id}/api-keys/usage",
        response_model=Dict,
        tags=["org_api_key_usage_stats"],
    )
    def get_usage(org_id: int, db: Session = Depends(get_test_session)):
        try:
            return org_api_key_usage_stats(db, org_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    app = FastAPI()
    app.include_router(router)

    # -----------------------------------------------------------------------
    # Test client assertions.
    client = TestClient(app)

    resp = client.get("/api/orgs/1/api-keys/usage")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["key_count"] == 2, "key_count mismatch"
    # Walk the JSON to ensure no 'key_hash' appears.
    def contains_key_hash(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "key_hash":
                    return True
                if contains_key_hash(v):
                    return True
        elif isinstance(obj, list):
            return any(contains_key_hash(item) for item in obj)
        return False

    assert not contains_key_hash(data), "'key_hash' leaked in response"
    print("PASS")