"""clerk_m2m_auth: the shared get_principal accepts a Clerk session JWT (user)
OR a Clerk M2M JWT (machine), with no live Clerk / JWKS / Postgres call.

A Clerk M2M token is a JWT signed by the SAME instance JWKS as session tokens,
carrying a machine subject (sub = "mch_...") and no "role" claim -- so the
existing JWKS decode already verifies it statelessly. The only new behaviour is
a machine short-circuit: a verified "mch_..." sub returns a read-only public
principal WITHOUT calling _resolve_role (whose Clerk users-API lookup errors on a
machine sub -> the live 500 this also fixes).

Two-pole: ACCEPT poles (machine JWT, user JWT) and REJECT poles (no creds, bad
decode) are both proven. All network (JWKS fetch, jwt.decode) is mocked.

Import-time note: both clerk_m2m_auth and its parent verdict_breakdown_api read
CLERK_PUBLISHABLE_KEY at import to derive the issuer. We set a deterministic test
publishable key on sys.path insert BEFORE importing so _CLERK_ISS is non-empty
(otherwise get_principal short-circuits to 503 "Auth not configured").
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# A valid Clerk-shaped publishable key whose middle segment base64-decodes to the
# issuer host (the module does `b64decode(pk.split("_")[2] + "===").rstrip("$")`).
_TEST_HOST = "clerk.example.com"
_TEST_PK = "pk_test_" + base64.b64encode((_TEST_HOST + "$").encode()).decode()
os.environ.setdefault("CLERK_PUBLISHABLE_KEY", _TEST_PK)

import clerk_m2m_auth as cm  # noqa: E402  (must follow the env/path setup above)

_EXPECTED_ISS = f"https://{_TEST_HOST}"


class _FakeSigningKey:
    key = "unused-mock-key"


def _mount(monkeypatch, *, session_claims, resolve_role_spy=None):
    """Build a one-route FastAPI app guarded by cm.get_principal with the Clerk
    JWKS + jwt.decode surface mocked.

    session_claims: dict -> jwt.decode returns it (a verified token);
                    None -> jwt.decode raises (bad/garbage token).
    resolve_role_spy: optional list; _resolve_role appends its sub arg so a test
                    can assert the Clerk users API was (not) consulted.
    """
    monkeypatch.setattr(cm, "_CLERK_ISS", _EXPECTED_ISS, raising=False)

    # JWKS: never hit the network -- always hand back a fake signing key.
    monkeypatch.setattr(cm, "_jwks_client",
                        lambda: type("J", (), {
                            "get_signing_key_from_jwt": staticmethod(
                                lambda tok: _FakeSigningKey())})())

    def _fake_decode(token, key, **kw):
        if session_claims is None:
            raise ValueError("invalid token")
        return session_claims
    monkeypatch.setattr(cm.jwt, "decode", _fake_decode)

    def _spy_resolve(sub):
        if resolve_role_spy is not None:
            resolve_role_spy.append(sub)
        return "insider"
    monkeypatch.setattr(cm, "_resolve_role", _spy_resolve)

    app = FastAPI()

    @app.get("/guarded")
    def guarded(principal=Depends(cm.get_principal)):
        return {"user_id": principal.user_id, "role": principal.role}

    return TestClient(app, raise_server_exceptions=True)


def test_machine_jwt_accepted_as_public_without_user_lookup(monkeypatch):
    """Verified JWT with sub 'mch_...' -> 200, role public, and _resolve_role
    (the Clerk users API) is NOT called -- this is the live-500 fix."""
    spy = []
    client = _mount(monkeypatch,
                    session_claims={"sub": "mch_3LwSNRGbLoekGqTXAPyKqkUtM4B", "aud": []},
                    resolve_role_spy=spy)
    r = client.get("/guarded", headers={"Authorization": "Bearer machine.jwt.token"})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": "mch_3LwSNRGbLoekGqTXAPyKqkUtM4B", "role": "public"}
    assert spy == [], "machine sub must not trigger a Clerk user lookup"


def test_user_jwt_still_resolves_via_role_path(monkeypatch):
    """A user_ sub with no role claim still resolves via _resolve_role (unchanged)."""
    spy = []
    client = _mount(monkeypatch,
                    session_claims={"sub": "user_abc"},  # no role claim
                    resolve_role_spy=spy)
    r = client.get("/guarded", headers={"Authorization": "Bearer session.jwt.token"})
    assert r.status_code == 200
    assert r.json() == {"user_id": "user_abc", "role": "insider"}  # from _resolve_role spy
    assert spy == ["user_abc"], "user sub must still consult the role resolver"


def test_user_jwt_with_role_claim_unchanged(monkeypatch):
    """An embedded role claim is honoured without any lookup (unchanged)."""
    spy = []
    client = _mount(monkeypatch,
                    session_claims={"sub": "user_xyz", "role": "admin"},
                    resolve_role_spy=spy)
    r = client.get("/guarded", headers={"Authorization": "Bearer session.jwt.token"})
    assert r.status_code == 200
    assert r.json() == {"user_id": "user_xyz", "role": "admin"}
    assert spy == [], "an embedded role claim must short-circuit the resolver"


def test_no_auth_header_is_401_authentication_required(monkeypatch):
    client = _mount(monkeypatch, session_claims=None)
    r = client.get("/guarded")
    assert r.status_code == 401
    assert r.json()["detail"] == "Authentication required"


def test_bad_token_fails_jwks_decode_401(monkeypatch):
    """A token that fails JWKS verification -> the original 401."""
    client = _mount(monkeypatch, session_claims=None)
    r = client.get("/guarded", headers={"Authorization": "Bearer garbage.token"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired session token"
