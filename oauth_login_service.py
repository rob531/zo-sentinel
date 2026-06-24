# deps: fastapi, pydantic, pyjwt, passlib
"""OAuth login service for Zo-Sentinel.

Provides a FastAPI APIRouter with authentication endpoints, JWT handling, and
password hashing. Designed to be importable without side effects; a __main__
block runs a self‑test using FastAPI's TestClient and an in‑memory store.
"""

import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
import jwt

# Password hashing: try passlib, else fallback to hashlib.pbkdf2_hmac
try:
    from passlib.context import CryptContext

    _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_password(pw: str) -> str:
        return _pwd_context.hash(pw)

    def verify_password(pw: str, hashed: str) -> bool:
        return _pwd_context.verify(pw, hashed)
except Exception:  # pragma: no cover
    import hashlib
    import base64
    import os as _os

    _salt_bytes = _os.urandom(16)

    def _hash(pw: str, salt: bytes) -> str:
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 100_000)
        return base64.b64encode(salt + dk).decode()

    def hash_password(pw: str) -> str:
        salt = _os.urandom(16)
        return _hash(pw, salt)

    def verify_password(pw: str, hashed: str) -> bool:
        data = base64.b64decode(hashed.encode())
        salt, dk = data[:16], data[16:]
        test = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 100_000)
        return test == dk

# JWT handling
JWT_SECRET = os.getenv("APP_JWT_SECRET", "dev-secret")  # dev default
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7


def _create_token(data: Dict[str, Any], expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALG)


def create_access_token(user_id: str, org_id: str, role: str) -> str:
    return _create_token({"sub": user_id, "org_id": org_id, "role": role, "type": "access"},
                         timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(user_id: str, org_id: str, role: str) -> str:
    return _create_token({"sub": user_id, "org_id": org_id, "role": role, "type": "refresh"},
                         timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))


def decode_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

# In‑memory session/store (swap‑out for real DB)
class Session:
    def __init__(self):
        # email -> user dict
        self.users: Dict[str, Dict[str, Any]] = {}
        # user_id -> email (reverse lookup)
        self.id_index: Dict[str, str] = {}

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return self.users.get(email)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        email = self.id_index.get(user_id)
        if email:
            return self.users.get(email)
        return None

    def create_user(self, email: str, password_hash: str, org_id: str, role: str = "user") -> Dict[str, Any]:
        if email in self.users:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")
        user_id = str(uuid.uuid4())
        user = {"id": user_id, "email": email, "password_hash": password_hash, "org_id": org_id, "role": role}
        self.users[email] = user
        self.id_index[user_id] = email
        return user

    def upsert_user_oauth(self, email: str, org_id: str, role: str = "user") -> Dict[str, Any]:
        # If exists, update org_id if needed; else create with random password hash
        user = self.users.get(email)
        if user:
            user["org_id"] = org_id
            return user
        # create with placeholder password hash
        placeholder_hash = hash_password(uuid.uuid4().hex)
        return self.create_user(email, placeholder_hash, org_id, role)

# Dependency
def get_session() -> Session:
    # In real deployment this would be a DB session; here we return a singleton per request.
    return session_singleton

# Global singleton for the in‑memory store (used by default dependency)
session_singleton = Session()

# Pydantic models (v2)
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    org_id: str

class RegisterResponse(BaseModel):
    user_id: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(BaseModel):
    refresh_token: str

class MeResponse(BaseModel):
    user_id: str
    org_id: str
    role: str

class OAuthCallbackRequest(BaseModel):
    code: str
    org_id: str

# Router
router = APIRouter(prefix="/auth", tags=["auth"])

# Helper to extract token principal
bearer_scheme = HTTPBearer(auto_error=False)

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
                     sess: Session = Depends(get_session)) -> Dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user = sess.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # Attach token claims for downstream use
    user["token_claims"] = payload
    return user

@router.post("/register", response_model=RegisterResponse)
def register(req: RegisterRequest, sess: Session = Depends(get_session)):
    pw_hash = hash_password(req.password)
    user = sess.create_user(email=req.email, password_hash=pw_hash, org_id=req.org_id)
    return RegisterResponse(user_id=user["id"])

@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, sess: Session = Depends(get_session)):
    user = sess.get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    access = create_access_token(user_id=user["id"], org_id=user["org_id"], role=user["role"])
    refresh = create_refresh_token(user_id=user["id"], org_id=user["org_id"], role=user["role"])
    return TokenResponse(access_token=access, refresh_token=refresh)

@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, sess: Session = Depends(get_session)):
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user = sess.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    access = create_access_token(user_id=user["id"], org_id=user["org_id"], role=user["role"])
    # Issue a new refresh token as well (optional, here we reuse the same)
    refresh = create_refresh_token(user_id=user["id"], org_id=user["org_id"], role=user["role"])
    return TokenResponse(access_token=access, refresh_token=refresh)

@router.get("/me", response_model=MeResponse)
def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    claims = current_user["token_claims"]
    return MeResponse(user_id=claims["sub"], org_id=claims["org_id"], role=claims["role"])

@router.post("/oauth/{provider}/callback", response_model=TokenResponse)
def oauth_callback(provider: str, req: OAuthCallbackRequest, sess: Session = Depends(get_session)):
    # In a real implementation we would exchange the code with the provider.
    # For this stub we derive an email from the code for deterministic testing.
    email = f"{req.code}@{provider}.example.com"
    user = sess.upsert_user_oauth(email=email, org_id=req.org_id)
    access = create_access_token(user_id=user["id"], org_id=user["org_id"], role=user["role"])
    refresh = create_refresh_token(user_id=user["id"], org_id=user["org_id"], role=user["role"])
    return TokenResponse(access_token=access, refresh_token=refresh)

# Self‑test harness
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    def _print(msg: str):
        print(msg)

    try:
        # 1. Register
        reg_resp = client.post("/auth/register", json={"email": "alice@example.com", "password": "s3cur3P@ss!", "org_id": "org1"})
        assert reg_resp.status_code == 200, f"Register failed: {reg_resp.text}"
        user_id = reg_resp.json()["user_id"]
        # 2. Login
        login_resp = client.post("/auth/login", json={"email": "alice@example.com", "password": "s3cur3P@ss!"})
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        tokens = login_resp.json()
        access_token = tokens["access_token"]
        # 3. Me with token
        me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert me_resp.status_code == 200, f"Me endpoint failed: {me_resp.text}"
        me_data = me_resp.json()
        assert me_data["user_id"] == user_id, "Me returned wrong user_id"
        # 4. Me without token
        unauth_resp = client.get("/auth/me")
        assert unauth_resp.status_code == 401, "Unauthenticated request did not 401"
        _print("PASS")
    except AssertionError as e:
        _print(f"FAIL:{e}")
        raise SystemExit(1)
