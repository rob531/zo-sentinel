
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.request
from datetime import date
from typing import Dict, List, Optional

import jwt
import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, or_, and_, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api/trust-verdict", tags=["trust_verdict"])

# Authentication configuration
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

def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return principal

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

# Response models
class AxisScore(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None

class Verdict(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    axes: Dict[str, AxisScore]
    model_overall_risk: Optional[str] = None
    published_overall_risk: Optional[str] = None
    trusted_override: bool = False
    override_reason: Optional[str] = None
    trusted: bool = False
    trust_basis: Optional[str] = None
    masquerade_flag: bool = False
    display_label: str = "Automated heuristic assessment"

# Utility functions
def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None

def _hit(db: Session, r, reveal: bool = True) -> dict:
    if not reveal:
        return {"server_id": r.server_id, "name": r.name, "url": r.url,
                "registry_source": r.registry_source,
                "published_overall_risk": None, "trusted": None, "hidden": True}
    lab = dict(db.execute(
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.label).where(
            McpLlmAxisScore.server_id == r.server_id,
            McpLlmAxisScore.axis_name.in_(("overall_risk", "maintainer_trust")))
    ).all())
    gate = trust_gate(r.url, r.name, {k: lab.get(k) for k in lab})
    return {"server_id": r.server_id, "name": r.name, "url": r.url,
            "registry_source": r.registry_source,
            "published_overall_risk": gate.get("published_overall_risk") or lab.get("overall_risk"),
            "trusted": bool(gate.get("trusted")), "hidden": False}

# Endpoints
@router.get("/verdict/{server_id}", response_model=Verdict)
def get_verdict(server_id: str, db: Session = Depends(get_session),
                principal: Principal = Depends(get_principal)) -> Verdict:
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")
    charge_lookup(db, principal)

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    axes: Dict[str, AxisScore] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        axes[r.axis_name] = AxisScore(axis_name=r.axis_name, label=r.label,
                                      label_index=r.label_index, p_top=r.p_top)
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)

    trusted = bool(gate.get("trusted"))
    trust_basis = gate.get("trust_basis")
    masquerade_flag = bool(gate.get("masquerade_flag"))
    trusted_override = bool(gate.get("trusted")) or bool(gate.get("masquerade_flag"))
    override_reason = trust_basis if trusted_override else None

    return Verdict(
        server_id=server_id, name=name, url=url, model_version=mv, axes=axes,
        model_overall_risk=gate.get("original_overall_risk") or labels.get("overall_risk"),
        published_overall_risk=gate.get("published_overall_risk") or labels.get("overall_risk"),
        trusted_override=trusted_override,
        override_reason=override_reason,
        trusted=trusted,
        trust_basis=trust_basis,
        masquerade_flag=masquerade_flag,
        display_label=gate.get("display_label", "Automated heuristic assessment"),
    )

@router.get("/servers")
def search_servers(q: str = "", risk: str = "", source: str = "",
                   limit: int = 30, offset: int = 0,
                   db: Session = Depends(get_session),
                   principal: Principal = Depends(get_principal)) -> dict:
    charge_lookup(db, principal)
    reveal = _reveal(principal)
    limit = max(1, min(limit, 100)); offset = max(0, offset)
    conds = []
    if q.strip():
        like = f"%{q.strip()}%"
        conds.append(or_(McpServerRegistry.name.ilike(like),
                         McpServerRegistry.url.ilike(like),
                         McpServerRegistry.server_id.ilike(like)))
    if source.strip():
        conds.append(McpServerRegistry.registry_source == source.strip())
    if risk.strip() and reveal:
        sub = select(McpLlmAxisScore.server_id).where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.label == risk.strip().upper())
        conds.append(McpServerRegistry.server_id.in_(sub))
    if not q.strip():
        conds.append(McpServerRegistry.name.isnot(None))
        conds.append(func.length(func.trim(McpServerRegistry.name)) >= 2)
    stmt = select(McpServerRegistry)
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(func.lower(McpServerRegistry.name)).offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()
    hits = [_hit(db, r, reveal) for r in rows]
    if risk.strip() and reveal:
        want = risk.strip().upper()
        hits = [h for h in hits if (h.get("published_overall_risk") or "").upper() == want]
    return {"servers": hits, "count": len(hits),
            "offset": offset, "limit": limit, "reveal": reveal}

@router.get("/me")
def me(principal: Principal = Depends(get_principal),
       db: Session = Depends(get_session)) -> dict:
    unlimited = principal.role in ("admin", "insider")
    used = db.execute(text("SELECT lookups FROM api_usage WHERE user_id=:u AND day=:d"),
                      {"u": principal.user_id, "d": date.today()}).scalar() or 0
    return {"role": principal.role, "unlimited": unlimited, "cap": LOOKUP_CAP,
            "used": used, "remaining": (None if unlimited else max(0, LOOKUP_CAP - used))}

@router.post("/submit")
def submit_mcp(payload: SubmitMCP, db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal)) -> dict:
    url = (payload.url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="A valid http(s) URL is required")
    sid = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()
    if db.get(McpServerRegistry, sid):
        return {"status": "exists", "server_id": sid}
    db.add(McpServerRegistry(server_id=sid, name=(payload.name or url)[:512], url=url,
                             registry_source="user_submission", verdict="unreviewed"))
    db.commit()
    return {"status": "submitted", "server_id": sid}

class SubmitMCP(BaseModel):
    url: str
    name: Optional[str] = None

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
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
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
    r = c.get("/api/trust-verdict/verdict/srv1"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["model_overall_risk"] == "HIGH", j
    assert j["published_overall_risk"] == "MEDIUM", j
    assert j["trusted"] is True, j
    assert len(j["axes"]) == 7, j
    assert c.get("/api/trust-verdict/verdict/nope").status_code == 404
    print("PASS")
