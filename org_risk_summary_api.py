# deps: requests
"""org_risk_summary_api.py -- FastAPI router exposing aggregated risk summary per org.
Mirrors the structure of verdict_breakdown_api.py.
"""
from __future__ import annotations

import os
import time
import json
import base64
import hashlib
import urllib.request
from datetime import date
from typing import Dict, List, Optional

import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func, and_, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Org
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["org"])

# Reuse the auth machinery from verdict_breakdown_api.py (copy-paste)
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

# ---------------------------------------------------------------------------
# Pydantic response model
class TierBreakdown(BaseModel):
    tier: str
    count: int

class HighestRiskServer(BaseModel):
    server_id: str
    name: Optional[str]
    p_top: Optional[float]

class OrgRiskSummary(BaseModel):
    org_id: str
    server_count: int
    tier_breakdown: Dict[str, int]
    mean_risk_score: Optional[float]
    median_risk_score: Optional[float]
    highest_risk_server: Optional[HighestRiskServer]
    last_updated: Optional[date]

# Helper functions -----------------------------------------------------------
def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return float((sorted_vals[mid - 1] + sorted_vals[mid]) / 2)

# ---------------------------------------------------------------------------
@router.get("/orgs/{org_id}/risk-summary", response_model=OrgRiskSummary)
def get_org_risk_summary(org_id: str,
                         db: Session = Depends(get_session),
                         principal: Principal = Depends(get_principal)) -> OrgRiskSummary:
    """Aggregated risk posture for an organization.
    Returns tier counts, mean/median of overall_risk p_top, and the highest‑risk server.
    """
    # Verify org exists
    org = db.get(Org, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    # Pull all server registry rows – in a real deployment this would be filtered by a perspective.
    servers = db.execute(select(McpServerRegistry)).scalars().all()
    # Filter to servers that belong to this org via a perspective (simplified: assume all belong).
    # In production a join with PerspectiveSnapshot would be used.
    server_ids = [s.server_id for s in servers]

    # Tier breakdown
    tier_counts: Dict[str, int] = {}
    for s in servers:
        tier = s.risk_tier or "UNKNOWN"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Scores for overall_risk axis
    scores = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.p_top)
        .where(
            McpLlmAxisScore.server_id.in_(server_ids),
            McpLlmAxisScore.axis_name == "overall_risk"
        )
    ).all()
    p_tops = [row.p_top for row in scores if row.p_top is not None]
    mean_score = float(sum(p_tops) / len(p_tops)) if p_tops else None
    median_score = _median(p_tops) if p_tops else None

    # Highest risk server (max p_top)
    highest = None
    if scores:
        max_row = max(scores, key=lambda r: (r.p_top or 0))
        srv = db.get(McpServerRegistry, max_row.server_id)
        highest = HighestRiskServer(
            server_id=max_row.server_id,
            name=srv.name if srv else None,
            p_top=max_row.p_top,
        )

    # Last updated – use the most recent scored_at among the selected scores
    last_updated = None
    if scores:
        latest = db.execute(
            select(func.max(McpLlmAxisScore.scored_at))
            .where(McpLlmAxisScore.server_id.in_(server_ids), McpLlmAxisScore.axis_name == "overall_risk")
        ).scalar()
        if latest:
            last_updated = latest.date()

    return OrgRiskSummary(
        org_id=org_id,
        server_count=len(servers),
        tier_breakdown=tier_counts,
        mean_risk_score=mean_score,
        median_risk_score=median_score,
        highest_risk_server=highest,
        last_updated=last_updated,
    )

# ---------------------------------------------------------------------------
if __name__ == "__main__":  # CI‑safe self‑test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # In‑memory SQLite engine
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    # Seed an Org
    s.add(Org(id="org1", name="Test Org"))
    # Seed 5 servers across 3 tiers
    tiers = ["TRUSTED_GENERAL", "MEDIUM_RISK", "HIGH_RISK"]
    for i, tier in enumerate(tiers * 2, start=1):
        sid = f"srv{i}"
        s.add(McpServerRegistry(server_id=sid, name=f"Server {i}", risk_tier=tier))
        # overall_risk score with varying p_top
        s.add(McpLlmAxisScore(
            id=i,
            server_id=sid,
            axis_name="overall_risk",
            label="MEDIUM",
            model_version="v3.0_40974559",
            p_top=10.0 * i,
        ))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    # Simple principal override – admin role to bypass auth caps
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="test", role="admin")

    client = TestClient(app)
    resp = client.get("/api/orgs/org1/risk-summary")
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}")
        exit(1)
    data = resp.json()
    if len(data["tier_breakdown"]) != 3:
        print("FAIL: tier_breakdown not 3 tiers")
        exit(1)
    if not (0 <= data.get("mean_risk_score", -1) <= 100):
        print("FAIL: mean_risk_score out of range")
        exit(1)
    print("PASS")
