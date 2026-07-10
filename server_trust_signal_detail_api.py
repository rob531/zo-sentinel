# deps: requests
"""FastAPI router: GET /servers/{server_id}/trust-signals.

Reads mcp_signal_scores rows from the ZoComputer store (write_service on :8772).
Returns signal list with evidence blobs, count, and avg confidence.
"""
from __future__ import annotations

import os
import time
import urllib.request
import base64
import json
from typing import Optional, List, Dict, Any

import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["trust-signals"])

_CLERK_PK = os.getenv("CLERK_PUBLISHABLE_KEY", "")
_CLERK_SK = os.getenv("CLERK_SECRET_KEY", "")


def _clerk_host() -> str:
    try:
        return base64.b64decode(_CLERK_PK.split("_")[2] + "===").decode().rstrip("$")
    except Exception:
        return ""


_CLERK_ISS = f"https://{_clerk_host()}" if _clerk_host() else ""
_jwks: Optional[PyJWKClient] = None


def _jwks_client() -> Optional[PyJWKClient]:
    global _jwks
    if _jwks is None and _CLERK_ISS:
        _jwks = PyJWKClient(_CLERK_ISS + "/.well-known/jwks.json", timeout=8, lifespan=3600)
    return _jwks


try:
    if _CLERK_ISS:
        _jwks_client().get_signing_keys()
except Exception:
    pass


_role_cache: Dict[str, tuple] = {}


def _resolve_role(sub: str) -> str:
    now = time.time()
    hit = _role_cache.get(sub)
    if hit and hit[1] > now:
        return hit[0]
    role = "public"
    try:
        req = urllib.request.Request(
            f"https://api.clerk.com/v1/users/{sub}",
            headers={"Authorization": f"Bearer {_CLERK_SK}",
                     "User-Agent": "mcplookup/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        cand = ((data.get("public_metadata") or {}).get("role") or "").strip().lower()
        if cand in ("admin", "insider", "public"):
            role = cand
    except Exception as exc:
        import sys
        print(f"[role-resolve] Clerk API lookup failed for {sub}: {exc}", file=sys.stderr)
    _role_cache[sub] = (role, now + 300)
    return role


class Principal(BaseModel):
    user_id: str
    role: str = "public"


_bearer = HTTPBearer(auto_error=False)


def get_principal(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Principal:
    if creds is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _CLERK_ISS:
        raise HTTPException(status_code=503, detail="Auth not configured")
    try:
        signing = _jwks_client().get_signing_key_from_jwt(creds.credentials)
        claims = jwt.decode(creds.credentials, signing.key, algorithms=["RS256"],
                            issuer=_CLERK_ISS, leeway=10, options={"verify_aud": False})
    except Exception:
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


class SignalEntry(BaseModel):
    signal_type: str
    confidence: float
    evidence_blob: Optional[dict] = None
    scored_at: Optional[str] = None


class TrustSignalsResponse(BaseModel):
    server_id: str
    signals: List[SignalEntry]
    signal_count: int
    avg_confidence: float


def _query_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    """Query mcp_signal_scores from ZoComputer store via write_service."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT signal_type, confidence, evidence_blob, scored_at FROM mcp_signal_scores WHERE server_id = $1", "params": [server_id]},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("rows", []) if isinstance(data, dict) else []
            return rows
        return []
    except Exception:
        return []


@router.get("/servers/{server_id}/trust-signals", response_model=TrustSignalsResponse)
def get_trust_signals(server_id: str, db: Session = Depends(get_session),
                      principal: Principal = Depends(get_principal)) -> TrustSignalsResponse:
    """Return all trust-signal scores for a server with evidence blobs."""
    rows = _query_signal_scores(server_id)

    signals = []
    total_conf = 0.0
    for row in rows:
        conf = float(row.get("confidence", 0.0) or 0.0)
        total_conf += conf
        signals.append(SignalEntry(
            signal_type=str(row.get("signal_type", "")),
            confidence=conf,
            evidence_blob=row.get("evidence_blob"),
            scored_at=str(row.get("scored_at", "")) if row.get("scored_at") else None,
        ))

    count = len(signals)
    avg_conf = total_conf / count if count > 0 else 0.0

    return TrustSignalsResponse(
        server_id=server_id,
        signals=signals,
        signal_count=count,
        avg_confidence=round(avg_conf, 4),
    )


if __name__ == "__main__":
    import requests as _req
    from unittest.mock import patch, MagicMock

    # Patch write_service to return mock signal scores
    def _mock_post(url, json=None, timeout=None):
        m = MagicMock()
        if "query" in url:
            params = json.get("params", []) if json else []
            server_id = params[0] if params else ""
            if server_id == "srv1":
                m.status_code = 200
                m.json = lambda: {"rows": [
                    {"signal_type": "repo_activity", "confidence": 0.85, "evidence_blob": {"stars": 1200}, "scored_at": "2026-07-10T10:00:00Z"},
                    {"signal_type": "maintainer_reputation", "confidence": 0.92, "evidence_blob": {"org": "stripe"}, "scored_at": "2026-07-10T10:00:00Z"},
                    {"signal_type": "dependency_health", "confidence": 0.78, "evidence_blob": {"vulns": 0}, "scored_at": "2026-07-10T10:00:00Z"},
                ]}
            else:
                m.status_code = 200
                m.json = lambda: {"rows": []}
        return m

    with patch("requests.post", side_effect=_mock_post):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router)

        def _override_session():
            yield MagicMock()

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")

        c = TestClient(app)

        # Happy path: signals returned with evidence blobs
        r = c.get("/api/servers/srv1/trust-signals")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["server_id"] == "srv1"
        assert j["signal_count"] == 3
        assert len(j["signals"]) == 3
        assert j["signals"][0]["evidence_blob"] is not None
        avg = (0.85 + 0.92 + 0.78) / 3
        assert abs(j["avg_confidence"] - round(avg, 4)) < 0.001, j
        assert j["signals"][0]["signal_type"] == "repo_activity"

        # Edge case: no signals for unknown server
        r2 = c.get("/api/servers/unknown_server/trust-signals")
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        assert j2["signal_count"] == 0
        assert j2["avg_confidence"] == 0.0
        assert j2["signals"] == []

        print("PASS")
