"""clerk_m2m_auth.py -- shared Clerk auth dependency for the gated /api/* routes.

Provides a single ``get_principal`` FastAPI dependency that accepts BOTH:

  1. a Clerk **session JWT** (the existing, unchanged user path), verified via the
     instance JWKS (RS256, issuer-pinned) and resolved to a role
     (admin / insider / public) exactly as ``verdict_breakdown_api`` does; and
  2. a Clerk **machine-to-machine (M2M) token**, so a machine / service identity
     can read the scoring routes.

Key fact (from decoding a real Clerk M2M token + a live backend test): the M2M
token is a *JWT* signed by the SAME instance JWKS and carrying the SAME ``iss``
as session tokens, with a machine subject (``sub`` = ``mch_...``), ``aud=[]`` and
NO ``role`` claim. So the existing
``jwt.decode(..., algorithms=["RS256"], issuer=_CLERK_ISS)`` ALREADY verifies it
statelessly -- no extra secret, no extra network call.

This module therefore only adds a MACHINE short-circuit: once the token verifies,
if its ``sub`` is a machine id (``mch_...``) we return a read-only ``public``
principal immediately, BEFORE the user role-claim / Clerk-users-API path. That
also FIXES the live 500 the gated routes currently return for a machine JWT:
``_resolve_role`` does a Clerk *user* lookup (``/v1/users/{sub}``) which errors
on a machine ``sub``, and that error was unhandled -> 500. The user path
(``user_...`` subs) is unchanged.

The session-JWT machinery (issuer derivation, JWKS client, role resolution and
the exact 401 detail strings) is REUSED from ``verdict_breakdown_api`` -- this
module does not re-implement or re-tune it, so existing user behaviour is
preserved byte-for-byte.
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

# Reuse the canonical session-JWT machinery -- single source of truth. These are
# the SAME objects the six per-module copies verify against, so the verification
# path (issuer pinning, JWKS, role resolution, 401 strings) is identical.
from verdict_breakdown_api import (
    Principal,
    _bearer,
    _jwks_client,
    _resolve_role,
    _CLERK_ISS,
)


def get_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    """Accept a Clerk session JWT (user) OR a Clerk M2M JWT (machine).

    Behaviour, in order:
      * No credentials             -> 401 "Authentication required" (unchanged).
      * Auth not configured        -> 503 "Auth not configured" (unchanged).
      * Token fails JWKS verify     -> 401 "Invalid or expired session token".
      * Verified, sub = "mch_..."   -> a read-only MACHINE principal (role
                                       "public"), BEFORE any user role lookup.
      * Verified, user sub          -> the existing user principal (admin /
                                       insider / public), unchanged.
    """
    if creds is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _CLERK_ISS:
        raise HTTPException(status_code=503, detail="Auth not configured")

    # Stateless JWKS verification -- already accepts BOTH session and M2M JWTs
    # (same instance issuer + signing keys). Behaviour preserved exactly.
    try:
        signing = _jwks_client().get_signing_key_from_jwt(creds.credentials)
        claims = jwt.decode(
            creds.credentials,
            signing.key,
            algorithms=["RS256"],
            issuer=_CLERK_ISS,
            leeway=10,
            options={"verify_aud": False},
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")

    # MACHINE short-circuit: a Clerk M2M token has a machine subject (mch_...) and
    # no "role" claim. Grant read-only least privilege and -- crucially -- do NOT
    # fall through to _resolve_role, whose Clerk /v1/users/{sub} lookup errors on
    # a machine sub (that unhandled error was the live 500 on the gated routes).
    if isinstance(sub, str) and sub.startswith("mch_"):
        return Principal(user_id=sub, role="public")

    # Existing user path (unchanged): prefer an embedded role claim, else resolve
    # the role from the Clerk users API.
    rc = claims.get("role")
    if not rc and isinstance(claims.get("public_metadata"), dict):
        rc = claims["public_metadata"].get("role")
    rc = (rc or "").strip().lower()
    role = rc if rc in ("admin", "insider", "public") else _resolve_role(sub)
    return Principal(user_id=sub, role=role)
