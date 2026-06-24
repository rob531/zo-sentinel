"""Auth primitives: password hashing + our-own JWT issuance/verification + the
`get_principal` dependency. We ISSUE and verify our own tokens (no external auth
server). Hashing prefers passlib/bcrypt and degrades to stdlib pbkdf2 so CI never
needs a native build.
"""
from __future__ import annotations
import hashlib
import hmac
import secrets
import time
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from .settings import settings

try:  # passlib pbkdf2 (pure-python, CI-robust) when available
    from passlib.context import CryptContext
    _pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

    def hash_password(p: str) -> str:
        return _pwd.hash(p)

    def verify_password(p: str, h: str) -> bool:
        try:
            return _pwd.verify(p, h)
        except Exception:
            return False
except Exception:  # stdlib fallback
    def hash_password(p: str) -> str:
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 200_000)
        return f"pbkdf2$200000${salt}${dk.hex()}"

    def verify_password(p: str, h: str) -> bool:
        try:
            _, iters, salt, hexd = h.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), int(iters))
            return hmac.compare_digest(dk.hex(), hexd)
        except Exception:
            return False


class Principal(BaseModel):
    user_id: str
    org_id: str
    role: str


def _make_token(sub: str, org_id: str, role: str, ttl: int, typ: str) -> str:
    now = int(time.time())
    payload = {"sub": sub, "org_id": org_id, "role": role, "type": typ,
               "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def create_access_token(user_id: str, org_id: str, role: str) -> str:
    return _make_token(user_id, org_id, role, settings.ACCESS_TTL, "access")


def create_refresh_token(user_id: str, org_id: str, role: str) -> str:
    return _make_token(user_id, org_id, role, settings.REFRESH_TTL, "refresh")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")


_bearer = HTTPBearer(auto_error=False)


def get_principal(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Principal:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    payload = decode_token(creds.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not an access token")
    return Principal(user_id=payload["sub"], org_id=payload["org_id"], role=payload["role"])
