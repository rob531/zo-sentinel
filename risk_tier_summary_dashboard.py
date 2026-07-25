"""FastAPI router exposing GET /risk_tier_summary -- risk tier distribution across the
scored registry. Reads from mcp_llm_axis_scores (axis_name='overall_risk') via the
app DB session. Mirrors verdict_breakdown_api.py patterns exactly."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from typing import Dict, Optional

import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk_tier_summary"])

ALL_TIERS = (
    "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT",
)

# ===================== Clerk auth =====================
LOOKUP_CAP = int(os.getenv("PUBLIC_LOOKUP_CAP", "20"))
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


# ===================== Pydantic response models =====================
class TierCount(BaseModel):
    tier: str
    count: int


class RiskTierSummary(BaseModel):
    tiers: Dict[str, int]
    total: int


# ===================== Endpoint =====================
@router.get("/risk_tier_summary", response_model=RiskTierSummary)
def get_risk_tier_summary(
        db: Session = Depends(get_session),
        principal: Principal = Depends(get_principal)) -> RiskTierSummary:
    """Count of servers per risk tier (overall_risk axis labels) across the scored registry."""
    rows = db.execute(
        select(McpLlmAxisScore.label, func.count(McpLlmAxisScore.server_id))
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .group_by(McpLlmAxisScore.label)
    ).all()

    counts: Dict[str, int] = {tier: 0 for tier in ALL_TIERS}
    for label, cnt in rows:
        if label in counts:
            counts[label] = cnt

    total = sum(counts.values())
    return RiskTierSummary(tiers=counts, total=total)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TS()
    # Seed: 3 servers across 3 different tiers
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="TRUSTED_GENERAL", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="overall_risk",
                          label="HIGH_RISK_ISOLATED", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv3", axis_name="overall_risk",
                          label="KNOWN_THREAT", model_version="v3.0_40974559"))
    # Non-overall_risk rows should be ignored
    s.add(McpLlmAxisScore(id=4, server_id="srv1", axis_name="auth_strength",
                          label="STRONG", model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")

    c = TestClient(app)
    r = c.get("/api/risk_tier_summary")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(ALL_TIERS) == set(j["tiers"].keys()), f"Missing tiers: {set(ALL_TIERS) - set(j['tiers'].keys())}"
    assert j["tiers"]["TRUSTED_GENERAL"] == 1, j
    assert j["tiers"]["HIGH_RISK_ISOLATED"] == 1, j
    assert j["tiers"]["KNOWN_THREAT"] == 1, j
    assert j["tiers"]["TRUSTED_RESEARCH"] == 0, j
    assert j["tiers"]["ENTERPRISE_CONTROLLED"] == 0, j
    assert j["tiers"]["CAUTION_LIMITED"] == 0, j
    assert j["tiers"]["INSUFFICIENT"] == 0, j
    assert j["total"] == 3, j
    print("PASS")
