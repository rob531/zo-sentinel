# deps: fastapi, sqlalchemy, pydantic
"""API Key Rotation Service.

Mirrors verdict_breakdown_api.py: FastAPI router + service class, real app.db/app.models
imports, Pydantic request/response models, dependency-injected session, and a
__main__ self-test that overrides get_session with an in-memory SQLite session.

Acceptance: __main__ block simulates key rotation and prints the new key.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

router = APIRouter(prefix="/api/keys", tags=["key-rotation"])

# ----------------------------- request / response models --------------------------

class RotateRequest(BaseModel):
    org_id: str
    label: Optional[str] = ""


class KeyRotationResult(BaseModel):
    org_id: str
    new_key_id: str
    new_key_prefix: str  # first 8 chars of raw key (shareable identifier)
    rotated_at: datetime


class KeyStatus(BaseModel):
    org_id: str
    total_keys: int
    keys: List[dict]


# ----------------------------- service class --------------------------------------

class ApiKeyRotationService:
    """Handles API key rotation for a given organization.

    Accepts a SQLAlchemy ``Session`` at construction (injected via
    FastAPI Depends(get_session) in production, or a test session in __main__).
    """

    def __init__(self, db: Session):
        self.db = db

    def _generate(self) -> tuple[str, str, str]:
        """Return (raw_key, key_hash, key_prefix)."""
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return raw_key, key_hash, raw_key[:8]

    def rotate_key(self, org_id: str, label: str = "") -> KeyRotationResult:
        """Rotate the active key for an org.

        * Validates the org exists.
        * Deletes every existing key for the org.
        * Generates a fresh key; stores only the SHA-256 hash.
        * Returns the new key's id, prefix (shareable), and rotation timestamp.
        """
        org = self.db.query(Org).filter(Org.id == org_id).first()
        if not org:
            raise ValueError(f"Org {org_id!r} not found")

        existing: List[ApiKey] = (
            self.db.query(ApiKey)
            .filter(ApiKey.org_id == org_id)
            .all()
        )
        for k in existing:
            self.db.delete(k)

        _raw, key_hash, key_prefix = self._generate()
        new_key = ApiKey(
            id=str(uuid.uuid4()),
            org_id=org_id,
            key_hash=key_hash,
            label=label or key_prefix,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(new_key)
        self.db.commit()
        self.db.refresh(new_key)

        return KeyRotationResult(
            org_id=org_id,
            new_key_id=new_key.id,
            new_key_prefix=key_prefix,
            rotated_at=new_key.created_at,
        )

    def get_status(self, org_id: str) -> KeyStatus:
        """Return summary of all keys for the org (most recent first)."""
        keys: List[ApiKey] = (
            self.db.query(ApiKey)
            .filter(ApiKey.org_id == org_id)
            .order_by(ApiKey.created_at.desc())
            .all()
        )
        return KeyStatus(
            org_id=org_id,
            total_keys=len(keys),
            keys=[
                {
                    "id": k.id,
                    "label": k.label,
                    "created_at": k.created_at.isoformat() if k.created_at else None,
                }
                for k in keys
            ],
        )


# ----------------------------- FastAPI endpoints ---------------------------------

@router.post("/rotate", response_model=KeyRotationResult)
def rotate(
    payload: RotateRequest,
    db: Session = Depends(get_session),
) -> KeyRotationResult:
    """Rotate the API key for an org. Any previous key is invalidated."""
    svc = ApiKeyRotationService(db)
    try:
        return svc.rotate_key(payload.org_id, payload.label)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/status/{org_id}", response_model=KeyStatus)
def status(
    org_id: str,
    db: Session = Depends(get_session),
) -> KeyStatus:
    """Return all API keys for the org (most recent first)."""
    svc = ApiKeyRotationService(db)
    return svc.get_status(org_id)


# ----------------------------- self-test ----------------------------------------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def _override():
        sess = TS()
        try:
            yield sess
        finally:
            sess.close()

    # Seed test org (only valid Org columns: id, name)
    seed = TS()
    org_id = str(uuid.uuid4())
    seed.add(Org(id=org_id, name="Test Org"))
    seed.commit()
    seed.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    c = TestClient(app)

    # ---- happy path: rotate twice; key count must stay at 1 ----
    r1 = c.post("/api/keys/rotate", json={"org_id": org_id})
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert len(j1["new_key_prefix"]) == 8, j1
    assert j1["org_id"] == org_id
    print(f"First rotation  -> new_key_prefix={j1['new_key_prefix']}")

    r2 = c.post("/api/keys/rotate", json={"org_id": org_id})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["new_key_id"] != j1["new_key_id"], "second key must differ from first"
    print(f"Second rotation -> new_key_prefix={j2['new_key_prefix']}")

    sr = c.get(f"/api/keys/status/{org_id}")
    assert sr.status_code == 200, sr.text
    js = sr.json()
    assert js["total_keys"] == 1, f"expected 1 key after 2 rotations, got {js['total_keys']}"
    assert js["keys"][0]["id"] == j2["new_key_id"]
    print("Key count after 2 rotations: 1 ✓")

    # ---- failure path: unknown org ----
    r_bad = c.post("/api/keys/rotate", json={"org_id": "does-not-exist"})
    assert r_bad.status_code == 404, r_bad.text
    print("Unknown org returns 404 ✓")

    print("PASS")
