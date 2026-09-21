"""server_risk_tier_detail_api.py -- Risk-tier grouped server listing with axis summaries.

Exposes GET /servers/risk-tier/{tier} returning all servers at a given risk_tier
with their 7-axis score summaries. Mirrors verdict_breakdown_api.py patterns.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from datetime import date
from typing import Dict, Optional

import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, and_, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk-tier"])

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


def _reveal(principal: Principal) -> bool:
    return principal.role in ("admin", "insider")


def charge_lookup(db: Session, principal: Principal, n: int = 1) -> None:
    if principal.role in ("admin", "insider"):
        return
    today = date.today()
    used = db.execute(text("SELECT lookups FROM api_usage WHERE user_id=:u AND day=:d"),
                      {"u": principal.user_id, "d": today}).scalar() or 0
    if used + n > LOOKUP_CAP:
        raise HTTPException(status_code=429,
            detail=f"Daily lookup limit reached ({LOOKUP_CAP}/day). Ask the chairman for insider access.")
    db.execute(text(
        "INSERT INTO api_usage(user_id, day, lookups) VALUES (:u,:d,:n) "
        "ON CONFLICT (user_id, day) DO UPDATE SET lookups = api_usage.lookups + :n"),
        {"u": principal.user_id, "d": today, "n": n})
    db.commit()


class AxisDetail(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class ServerRiskDetail(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    axes: Dict[str, AxisDetail]
    overall_risk: AxisDetail
    last_assessed: Optional[str] = None


class RiskTierResponse(BaseModel):
    tier: str
    server_count: int
    servers: list[ServerRiskDetail]


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/risk-tier/{tier}", response_model=RiskTierResponse)
def get_servers_by_risk_tier(
        tier: str,
        db: Session = Depends(get_session),
        principal: Principal = Depends(get_principal)) -> RiskTierResponse:
    """All servers classified at a given risk_tier with their axis score summaries."""
    charge_lookup(db, principal)

    tier_upper = tier.strip().upper()
    if not tier_upper:
        raise HTTPException(status_code=400, detail="Tier parameter is required")

    rows = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.risk_tier == tier_upper)
    ).scalars().all()

    servers: list[ServerRiskDetail] = []
    for reg in rows:
        mv = _latest_model_version(db, reg.server_id)
        if mv is None:
            continue

        axis_rows = db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id == reg.server_id,
                McpLlmAxisScore.model_version == mv,
            )
        ).scalars().all()

        axes: Dict[str, AxisDetail] = {}
        labels: Dict[str, str] = {}
        overall_risk = AxisDetail()
        for r in axis_rows:
            ad = AxisDetail(label=r.label, p_top=r.p_top,
                            p_critical=r.p_critical, p_danger=r.p_danger)
            axes[r.axis_name] = ad
            if r.label:
                labels[r.axis_name] = r.label
            if r.axis_name == "overall_risk":
                overall_risk = ad

        # Apply trust-gating override to published overall_risk for display only;
        # the registry risk_tier is the authoritative classification for the query.
        gate = trust_gate(reg.url, reg.name, labels)
        pub_overall = gate.get("published_overall_risk") or labels.get("overall_risk")

        servers.append(ServerRiskDetail(
            server_id=reg.server_id,
            name=reg.name,
            verdict=reg.verdict,
            axes=axes,
            overall_risk=overall_risk,
            last_assessed=reg.last_assessed.isoformat() if reg.last_assessed else None,
        ))

    return RiskTierResponse(tier=tier_upper, server_count=len(servers), servers=servers)


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

    s.add(McpServerRegistry(server_id="srv1", name="Alpha Server",
                            url="https://github.com/example/alpha",
                            risk_tier="CAUTION_LIMITED", verdict="reviewed"))
    s.add(McpServerRegistry(server_id="srv2", name="Beta Server",
                            url="https://github.com/example/beta",
                            risk_tier="CAUTION_LIMITED", verdict="reviewed"))

    for _i, (ax, lbl) in enumerate((
            ("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
            ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
            ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    for _i, (ax, lbl) in enumerate((
            ("overall_risk", "LOW"), ("auth_strength", "STRONG"),
            ("capability_breadth", "NARROW"), ("data_sensitivity", "LOW"),
            ("network_egress", "LOCAL"), ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MINIMAL")), start=100):
        s.add(McpLlmAxisScore(id=_i, server_id="srv2", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))

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

    r = c.get("/api/servers/risk-tier/CAUTION_LIMITED")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tier"] == "CAUTION_LIMITED", j
    assert j["server_count"] == 2, j
    assert len(j["servers"]) == 2, j
    for srv in j["servers"]:
        assert len(srv["axes"]) == 7, f"{srv['server_id']} missing axes: {srv['axes'].keys()}"
    assert c.get("/api/servers/risk-tier/CRITICAL").status_code == 200
    crit = c.get("/api/servers/risk-tier/CRITICAL").json()
    assert crit["server_count"] == 0, crit

    print("PASS")