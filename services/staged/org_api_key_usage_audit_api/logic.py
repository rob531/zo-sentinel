"""logic.py -- data/computation layer for org_api_key_usage_audit_api.

Provides a read‑only view of API‑key usage per organisation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import ApiKey, Org, User

# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def get_org_api_key_audit(db: Session, org_id: str) -> Dict[str, Any]:
    """Return audit information for an organisation's API keys.

    The response shape matches the contract described in the task
    specification.
    """
    org = db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")

    # Fetch all API keys belonging to the organisation
    key_rows = db.execute(
        select(ApiKey).where(ApiKey.org_id == org_id)
    ).scalars().all()

    # Build the list of key dicts
    keys: List[Dict[str, Any]] = []
    owner_emails: set[str] = set()
    for key in key_rows:
        # Owner information – there is no direct FK, so we leave it null
        owner_email: Optional[str] = None
        owner_role: Optional[str] = None

        # Record distinct owners when we have an email
        if owner_email:
            owner_emails.add(owner_email)

        keys.append(
            {
                "id": str(key.id),
                "label": key.label,
                "created_at": key.created_at.isoformat(),
                "owner_email": owner_email,
                "owner_role": owner_role,
                "key_hash_prefix": key.key_hash[:8] if key.key_hash else None,
            }
        )

    # Counts
    total_keys = len(keys)
    distinct_owners = len(owner_emails)
    unlabelled = sum(1 for k in key_rows if not (k.label and k.label.strip()))

    # Oldest creation timestamp
    oldest_created_at: Optional[str] = None
    if key_rows:
        oldest = min(k.created_at for k in key_rows)
        oldest_created_at = oldest.isoformat()

    return {
        "org_id": str(org.id),
        "org_name": org.name,
        "keys": keys,
        "counts": {
            "keys": total_keys,
            "distinct_owners": distinct_owners,
            "unlabelled": unlabelled,
        },
        "oldest_created_at": oldest_created_at,
    }

# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test)
    # ------------------------------------------------------------------- #
    from sqlalchemy.pool import StaticPool

    TEST_ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import the declarative base from the app package and create tables
    from app.db import Base  # type: ignore

    Base.metadata.create_all(TEST_ENGINE)
    TestSession = sessionmaker(bind=TEST_ENGINE)

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    session = TestSession()
    now = datetime.utcnow()

    # Organisations
    org1 = Org(id="org1", name="Org One", created_at=now)
    org2 = Org(id="org2", name="Org Two", created_at=now)
    session.add_all([org1, org2])

    # Users (only one user for org1)
    user1 = User(
        id="user1",
        email="alice@example.com",
        role="admin",
        org_id="org1",
        password_hash="hashed",
        created_at=now,
    )
    session.add(user1)

    # API keys (three for org1)
    key1 = ApiKey(
        id="key1",
        org_id="org1",
        key_hash="abcd1234efgh5678",
        label="First key",
        created_at=now,
    )
    # Unlabelled key (label set to empty string to simulate NULL/blank)
    key2 = ApiKey(
        id="key2",
        org_id="org1",
        key_hash="ijkl9012mnop3456",
        label="",
        created_at=now,
    )
    # Key whose owner does not exist (no user linked)
    key3 = ApiKey(
        id="key3",
        org_id="org1",
        key_hash="qrst7890uvwx1234",
        label="Third key",
        created_at=now,
    )
    session.add_all([key1, key2, key3])
    session.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()

    @app.get("/api/orgs/{org_id}/api-key-audit")
    def audit_endpoint(org_id: str, db: Session = Depends(get_session)):
        return get_org_api_key_audit(db, org_id)

    # Override the real DB dependency with the test session
    app.dependency_overrides[get_session] = lambda: session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    resp = client.get("/api/orgs/org1/api-key-audit")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Required assertions
    assert data["counts"]["keys"] == 3, "Key count mismatch"
    assert data["counts"]["unlabelled"] == 1, "Unlabelled count mismatch"

    # Ensure no full key_hash appears in the payload
    for full_hash in ("abcd1234efgh5678", "ijkl9012mnop3456", "qrst7890uvwx1234"):
        assert full_hash not in str(data), f"Full key_hash {full_hash} leaked"

    print("PASS")