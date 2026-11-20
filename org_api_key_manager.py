"""org_api_key_manager.py -- APIKeyManager utility + admin REST router for local accounts & API keys."""
from __future__ import annotations

import hashlib
import os
import random
import secrets
import string
import time
import uuid
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User
from app.security import hash_password
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(tags=["admin-accounts"])
_ALLOWED_ROLES = ("viewer", "member", "admin")


class APIKeyManager:
    def __init__(self, keys_file: str = "api_keys.txt"):
        self.keys_file = keys_file
        self._ensure_keys_file()
        self.rate_limits = {}  # key_id: (timestamp, count)

    def _ensure_keys_file(self):
        if not os.path.exists(self.keys_file):
            with open(self.keys_file, "w", encoding="utf-8"):
                os.chmod(self.keys_file, 0o600)

    def _generate_key(self, length: int = 32) -> str:
        chars = string.ascii_letters + string.digits
        return "".join(random.choice(chars) for _ in range(length))

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def issue_key(self, org_id: str, label: str) -> Tuple[str, str]:
        key = self._generate_key()
        key_hash = self._hash_key(key)
        with open(self.keys_file, "a", encoding="utf-8") as f:
            f.write(f"{key_hash},{org_id},{label}\n")
        return key_hash, key

    def revoke_key(self, key_id: str) -> bool:
        try:
            with open(self.keys_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(self.keys_file, "w", encoding="utf-8") as f:
                for line in lines:
                    if not line.startswith(key_id):
                        f.write(line)
            if self.rate_limits.get(key_id):
                del self.rate_limits[key_id]
            return True
        except FileNotFoundError:
            return False

    def verify_key(self, key: str) -> Optional[str]:
        key_hash = self._hash_key(key)
        try:
            with open(self.keys_file, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) == 3 and parts[0] == key_hash:
                        return parts[1]  # org_id
        except FileNotFoundError:
            pass
        return None

    def rate_limit_check(self, key_id: str, limit: int = 100, window: int = 60) -> bool:
        now = int(time.time())
        if key_id not in self.rate_limits:
            self.rate_limits[key_id] = (now, 1)
            return True
        timestamp, count = self.rate_limits[key_id]
        if now - timestamp > window:
            self.rate_limits[key_id] = (now, 1)
            return True
        if count < limit:
            self.rate_limits[key_id] = (timestamp, count + 1)
            return True
        return False


class ApiKeyCreateRequest(BaseModel):
    org_id: str = "default"
    org_name: Optional[str] = None
    label: str


class AdminUserCreateRequest(BaseModel):
    email: str
    password: str
    org_id: str = "default"
    org_name: Optional[str] = None
    role: str = "member"


@router.get("/api/admin/api-keys")
def list_api_keys(
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """List issued API keys (hash/secret never returned after issuance)."""
    rows = db.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars().all()
    return {
        "api_keys": [
            {
                "id": k.id,
                "org_id": k.org_id,
                "label": k.label,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in rows
        ]
    }


@router.post("/api/admin/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(
    req: ApiKeyCreateRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Mint a one-time API key stored by SHA-256 hash in `api_keys`."""
    label = (req.label or "").strip()[:128]
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    org_id = (req.org_id or "default").strip()[:64] or "default"
    if db.get(Org, org_id) is None:
        db.add(Org(id=org_id, name=(req.org_name or org_id)[:255]))

    raw_token = f"mcprk_{secrets.token_urlsafe(24)}"
    key_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    rec = ApiKey(id=f"key_{uuid.uuid4().hex[:16]}", org_id=org_id, key_hash=key_hash, label=label)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {
        "id": rec.id,
        "org_id": rec.org_id,
        "label": rec.label,
        "api_key": raw_token,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


@router.delete("/api/admin/api-keys/{key_id}")
def revoke_api_key(
    key_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Revoke/delete an API key row."""
    rec = db.get(ApiKey, key_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="API key not found")
    db.delete(rec)
    db.commit()
    return {"status": "revoked", "id": key_id}


@router.get("/api/admin/users")
def list_users(
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """List local service/team user accounts."""
    rows = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "org_id": u.org_id,
                "role": u.role,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ]
    }


@router.post("/api/admin/users", status_code=status.HTTP_201_CREATED)
def create_user(
    req: AdminUserCreateRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Provision a password-hashed local service or team user in `users`."""
    email = (req.email or "").strip().lower()[:255]
    if "@" not in email or len(req.password or "") < 12:
        raise HTTPException(status_code=400, detail="Valid email and >=12-char password required")
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    org_id = (req.org_id or "default").strip()[:64] or "default"
    if db.get(Org, org_id) is None:
        db.add(Org(id=org_id, name=(req.org_name or org_id)[:255]))
    role = req.role if req.role in _ALLOWED_ROLES else "member"
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=hash_password(req.password),
        org_id=org_id,
        role=role,
    )
    db.add(user)
    db.commit()
    return {"user_id": user.id, "email": user.email, "org_id": user.org_id, "role": user.role}


if __name__ == "__main__":
    import tempfile
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    with tempfile.TemporaryDirectory() as td:
        mgr = APIKeyManager(keys_file=os.path.join(td, "keys.txt"))
        kid, raw = mgr.issue_key("test_org", "test_label")
        assert mgr.verify_key(raw) == "test_org"
        mgr.revoke_key(kid)
        assert mgr.verify_key(raw) is None
        assert mgr.rate_limit_check("k1", limit=2, window=60) is True

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng)

    def _override_session():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_session
    test_app.dependency_overrides[require_admin] = lambda: Principal(user_id="adm", role="admin")

    c = TestClient(test_app)
    rk = c.post("/api/admin/api-keys", json={"org_id": "zo-org", "label": "ci-reader"})
    assert rk.status_code == 201, rk.text
    issued_id = rk.json()["id"]
    assert rk.json()["api_key"].startswith("mcprk_")
    assert len(c.get("/api/admin/api-keys").json()["api_keys"]) == 1

    ru = c.post("/api/admin/users", json={"email": "argon-svc@mcprisky.io", "password": "SuperSecretPassword123!", "org_id": "zo-org", "role": "member"})
    assert ru.status_code == 201, ru.text
    assert len(c.get("/api/admin/users").json()["users"]) == 1

    rd = c.delete(f"/api/admin/api-keys/{issued_id}")
    assert rd.status_code == 200
    print("PASS")
