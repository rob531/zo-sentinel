"""Authentication router -- register/login/refresh/me + OAuth-callback stub --
backed by the ORM and our-own JWTs. Adapted from the M3-built oauth_login_service
to use the shared SQLAlchemy session + models instead of an ad-hoc store.
"""
from __future__ import annotations
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Org, User
from .security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
    get_principal, Principal,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_ROLES = ("viewer", "member", "admin")


class RegisterRequest(BaseModel):
    email: str
    password: str
    org_id: str
    org_name: Optional[str] = None
    role: str = "member"


class RegisterResponse(BaseModel):
    user_id: str
    org_id: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: str
    org_id: str
    role: str


class OAuthCallbackRequest(BaseModel):
    code: str
    org_id: str


def _by_email(sess: Session, email: str) -> Optional[User]:
    return sess.execute(select(User).where(User.email == email)).scalar_one_or_none()


def _tokens(u: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(u.id, u.org_id, u.role),
        refresh_token=create_refresh_token(u.id, u.org_id, u.role),
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, sess: Session = Depends(get_session)):
    if _by_email(sess, req.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")
    if sess.get(Org, req.org_id) is None:
        sess.add(Org(id=req.org_id, name=req.org_name or req.org_id))
    user = User(
        id=str(uuid.uuid4()), email=req.email, password_hash=hash_password(req.password),
        org_id=req.org_id, role=req.role if req.role in _ROLES else "member",
    )
    sess.add(user)
    sess.commit()
    return RegisterResponse(user_id=user.id, org_id=user.org_id)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, sess: Session = Depends(get_session)):
    user = _by_email(sess, req.email)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    return _tokens(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, sess: Session = Depends(get_session)):
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not a refresh token")
    user = sess.get(User, payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return _tokens(user)


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)):
    return MeResponse(user_id=principal.user_id, org_id=principal.org_id, role=principal.role)


@router.post("/oauth/{provider}/callback", response_model=TokenResponse)
def oauth_callback(provider: str, req: OAuthCallbackRequest, sess: Session = Depends(get_session)):
    # MVP: a real impl exchanges `code` with the provider; we derive a deterministic
    # email so the flow is testable. Upsert the user, then issue OUR tokens.
    email = f"{req.code}@{provider}.oauth.local"
    user = _by_email(sess, email)
    if user is None:
        if sess.get(Org, req.org_id) is None:
            sess.add(Org(id=req.org_id, name=req.org_id))
        user = User(id=str(uuid.uuid4()), email=email, password_hash="!oauth",
                    org_id=req.org_id, role="member")
        sess.add(user)
        sess.commit()
    return _tokens(user)
