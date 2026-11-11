"""clerk_m2m_auth: the shared get_principal accepts a Clerk session JWT OR a
Clerk M2M token, without any live Clerk / JWKS / Postgres call.

Two-pole: both the ACCEPT poles (valid session JWT, valid M2M token) and the
REJECT poles (no creds, garbage token, M2M disabled) are proven. All network
(JWKS fetch, jwt.decode, the Clerk M2M verify POST) is mocked.

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


def _mount(monkeypatch, *, machine_sk: str = "", session_claims=None, m2m_status=None,
           m2m_body=None):
    """Build a one-route FastAPI app guarded by cm.get_principal with the Clerk
    network surface mocked.

    session_claims: dict -> jwt.decode returns it; None -> jwt.decode raises
                    (simulates an invalid/garbage session token).
    m2m_status:     int  -> requests.post returns this status with m2m_body json;
                    None -> the M2M verify endpoint is never expected to succeed
                            (we still install a 401 responder so a stray call fails).
    """
    monkeypatch.setattr(cm, "_CLERK_ISS", _EXPECTED_ISS, raising=False)
    monkeypatch.setattr(cm, "_CLERK_MACHINE_SK", machine_sk, raising=False)

    # JWKS: never hit the network -- always hand back a fake signing key.
    monkeypatch.setattr(cm, "_jwks_client",
                        lambda: type("J", (), {
                            "get_signing_key_from_jwt": staticmethod(
                                lambda tok: _FakeSigningKey())})())

    # jwt.decode: either return the session claims, or raise (bad token).
    def _fake_decode(token, key, **kw):
        if session_claims is None:
            raise ValueError("invalid session token")
        return session_claims
    monkeypatch.setattr(cm.jwt, "decode", _fake_decode)

    # Role resolution must never hit Clerk's users API in a unit test.
    monkeypatch.setattr(cm, "_resolve_role", lambda sub: "public")

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body or {}

        def json(self):
            return self._body

    def _fake_post(url, **kw):
        assert url == cm._M2M_VERIFY_URL
        assert kw["headers"]["Authorization"] == f"Bearer {machine_sk}"
        assert kw["json"] == {"token": _sent_token}
        status = m2m_status if m2m_status is not None else 401
        return _Resp(status, m2m_body)
    monkeypatch.setattr(cm.requests, "post", _fake_post)

    app = FastAPI()

    @app.get("/guarded")
    def guarded(principal=Depends(cm.get_principal)):
        return {"user_id": principal.user_id, "role": principal.role}

    return TestClient(app, raise_server_exceptions=True)


# Captured so the mocked requests.post can assert the body it received.
_sent_token = "m2m_tok_abc"


def test_valid_m2m_token_accepted_as_public_machine(monkeypatch):
    """Session JWT fails, M2M verify -> 200: machine principal, role public."""
    client = _mount(monkeypatch, machine_sk="sk_machine_xyz",
                    session_claims=None,  # session path fails -> fall to M2M
                    m2m_status=200, m2m_body={"subject": "mch_123"})
    r = client.get("/guarded", headers={"Authorization": f"Bearer {_sent_token}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": "mch_123", "role": "public"}


def test_m2m_id_falls_back_to_id_field(monkeypatch):
    client = _mount(monkeypatch, machine_sk="sk_machine_xyz",
                    session_claims=None, m2m_status=200, m2m_body={"id": "mch_fromid"})
    r = client.get("/guarded", headers={"Authorization": f"Bearer {_sent_token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "mch_fromid"
    assert r.json()["role"] == "public"


def test_no_auth_header_is_401_authentication_required(monkeypatch):
    client = _mount(monkeypatch, machine_sk="sk_machine_xyz", session_claims=None)
    r = client.get("/guarded")
    assert r.status_code == 401
    assert r.json()["detail"] == "Authentication required"


def test_garbage_token_rejected_401(monkeypatch):
    """Session decode raises AND M2M verify -> non-200: the original 401."""
    client = _mount(monkeypatch, machine_sk="sk_machine_xyz",
                    session_claims=None, m2m_status=403)
    r = client.get("/guarded", headers={"Authorization": f"Bearer {_sent_token}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired session token"


def test_valid_session_jwt_still_works(monkeypatch):
    """Prove the existing session-JWT path is unbroken: valid claims -> principal."""
    client = _mount(monkeypatch, machine_sk="sk_machine_xyz",
                    session_claims={"sub": "user_abc", "role": "admin"})
    r = client.get("/guarded", headers={"Authorization": "Bearer session.jwt.token"})
    assert r.status_code == 200
    assert r.json() == {"user_id": "user_abc", "role": "admin"}


def test_machine_secret_unset_rejects_m2m_session_only(monkeypatch):
    """CLERK_MACHINE_SECRET_KEY unset -> M2M fallback skipped, no crash, 401."""
    # machine_sk empty: _verify_m2m returns None immediately; requests.post must
    # NOT be called (we make it explode to prove the short-circuit).
    client = _mount(monkeypatch, machine_sk="", session_claims=None)

    def _boom(*a, **k):
        raise AssertionError("requests.post must not be called when machine SK is unset")
    monkeypatch.setattr(cm.requests, "post", _boom)

    r = client.get("/guarded", headers={"Authorization": f"Bearer {_sent_token}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired session token"


def test_m2m_network_error_fails_closed(monkeypatch):
    """A raising requests.post (network error) fails closed to the session 401."""
    client = _mount(monkeypatch, machine_sk="sk_machine_xyz", session_claims=None)

    def _raise(*a, **k):
        raise ConnectionError("clerk unreachable")
    monkeypatch.setattr(cm.requests, "post", _raise)

    r = client.get("/guarded", headers={"Authorization": f"Bearer {_sent_token}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired session token"
