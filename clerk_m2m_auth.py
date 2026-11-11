# deps: requests
"""clerk_m2m_auth.py -- shared Clerk auth dependency for the gated /api/* routes.

Provides a single ``get_principal`` FastAPI dependency that accepts BOTH:

  1. a Clerk **session JWT** (the existing, unchanged path), verified via the
     instance JWKS (RS256, issuer-pinned) and resolved to a role
     (admin / insider / public) exactly as ``verdict_breakdown_api`` does; and
  2. a Clerk **machine-to-machine (M2M) token**, so a machine / service identity
     can read the scoring routes. When the session-JWT path fails, the token is
     offered to Clerk's M2M verify endpoint
     (``POST https://api.clerk.com/v1/m2m_tokens/verify``, authorised with the
     NEW ``CLERK_MACHINE_SECRET_KEY``). On HTTP 200 the caller is treated as a
     valid MACHINE principal -- returned as role ``public`` (read-only least
     privilege). On any non-200 we fall through to the original
     ``401 "Invalid or expired session token"``.

The session-JWT machinery (issuer derivation, JWKS client, role resolution and
the exact 401 detail strings) is REUSED from ``verdict_breakdown_api`` -- this
module does not re-implement or re-tune it, so the existing behaviour is
preserved byte-for-byte. If ``CLERK_MACHINE_SECRET_KEY`` is unset/empty the M2M
fallback is simply skipped and this behaves identically to the session-only
``verdict_breakdown_api.get_principal``.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import jwt
import requests
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

# Reuse the canonical session-JWT machinery -- single source of truth. These are
# the SAME objects the six per-module copies verify against, so the session path
# (issuer pinning, JWKS, role resolution, 401 strings) is identical.
from verdict_breakdown_api import (
    Principal,
    _bearer,
    _jwks_client,
    _resolve_role,
    _CLERK_ISS,
)

# NEW config: the Clerk *machine* secret key. Deployer sets it as a Fly secret on
# the mcplookup app. Unset/empty -> M2M fallback skipped (session-only behaviour).
_CLERK_MACHINE_SK = os.getenv("CLERK_MACHINE_SECRET_KEY", "")

# Clerk's M2M token verification endpoint.
_M2M_VERIFY_URL = "https://api.clerk.com/v1/m2m_tokens/verify"


def _machine_id_from(payload: dict) -> str:
    """Best-effort machine identity from a successful M2M verify response.

    Clerk's M2M verify payload identifies the token subject via ``subject``
    (falling back to ``id``). We never fabricate an identity -- if neither is
    present we use a stable sentinel so the principal is still clearly a machine.
    """
    for key in ("subject", "id"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return "machine"


def _verify_m2m(token: str) -> Optional[Principal]:
    """Offer ``token`` to Clerk's M2M verify endpoint.

    Returns a MACHINE ``Principal`` (role ``public``, read-only least privilege)
    on HTTP 200, or ``None`` if the fallback is disabled, the token is rejected,
    or the call errors (fail-closed -- the caller then raises the normal 401).
    """
    if not _CLERK_MACHINE_SK:
        return None
    try:
        resp = requests.post(
            _M2M_VERIFY_URL,
            headers={
                "Authorization": f"Bearer {_CLERK_MACHINE_SK}",
                "User-Agent": "mcplookup/1.0",
            },
            json={"token": token},
            timeout=5,
        )
    except Exception as exc:  # network error -> fail-closed to session 401
        print(f"[m2m-verify] Clerk M2M verify call failed: {exc}", file=sys.stderr)
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return Principal(user_id=_machine_id_from(payload), role="public")


def get_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    """Accept a Clerk session JWT OR a Clerk M2M token.

    Behaviour, in order:
      * No credentials            -> 401 "Authentication required" (unchanged).
      * Auth not configured       -> 503 "Auth not configured" (unchanged).
      * Valid session JWT         -> the existing session principal (admin /
                                     insider / public), unchanged.
      * Session JWT fails, but    -> a MACHINE principal (role "public").
        a valid M2M token
      * Neither verifies          -> 401 "Invalid or expired session token".
    """
    if creds is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _CLERK_ISS:
        raise HTTPException(status_code=503, detail="Auth not configured")

    # 1) Existing Clerk session-JWT path (behaviour preserved exactly).
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
        # 2) Fall back to Clerk M2M verification before failing.
        machine = _verify_m2m(creds.credentials)
        if machine is not None:
            return machine
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")
    rc = claims.get("role")
    if not rc and isinstance(claims.get("public_metadata"), dict):
        rc = claims["public_metadata"].get("role")
    rc = (rc or "").strip().lower()
    role = rc if rc in ("admin", "insider", "public") else _resolve_role(sub)
    return Principal(user_id=sub, role=role)
