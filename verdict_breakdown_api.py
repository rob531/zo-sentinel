"""verdict_breakdown_api.py -- REAL per-server verdict endpoint (Tier-2 MVP).

Reads the SFT risk scores from Postgres (McpLlmAxisScore, ~65k rows) + registry
metadata (McpServerRegistry), and applies the trust-gating override so official
publishers (Stripe / Microsoft / Google ...) are NOT shown as false HIGH/CRITICAL.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
This is the data-wired reference the fixed webapp_backend_fastapi recipe should now
produce: it imports the REAL app data layer (app.db / app.models) -- no inline stubs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.request
from datetime import date
from typing import Dict, Optional

import jwt
import requests
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, or_, and_, func, text, exists
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

import logging

_audit_log = logging.getLogger("zo.audit.trust_gating")

router = APIRouter(prefix="/api", tags=["verdict"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

# ===================== Clerk auth + role-based rate limiting =====================
# All /api/* data endpoints require a valid Clerk session token (no token -> 401),
# which prevents anonymous bot scraping. Roles (Clerk publicMetadata.role):
#   admin   -> everything, unlimited, admin UI
#   insider -> unlimited lookups, full reveal
#   public  -> capped daily lookups (verdict opens + searches); risk tiers hidden in lists
LOOKUP_CAP = int(os.getenv("PUBLIC_LOOKUP_CAP", "20"))
_CLERK_PK = os.getenv("CLERK_PUBLISHABLE_KEY", "")
_CLERK_SK = os.getenv("CLERK_SECRET_KEY", "")


def _clerk_host() -> str:
    try:
        return base64.b64decode(_CLERK_PK.split("_")[2] + "===").decode().rstrip("$")
    except Exception:
        return ""


_CLERK_ISS = f"https://{_clerk_host()}" if _CLERK_PK and _clerk_host() else ""
_jwks: Optional[PyJWKClient] = None


def _jwks_client() -> Optional[PyJWKClient]:
    global _jwks
    if _jwks is None and _CLERK_ISS:
        # bounded timeout + long-lived cache so a cold/slow JWKS fetch can't hang a worker
        _jwks = PyJWKClient(_CLERK_ISS + "/.well-known/jwks.json", timeout=8, lifespan=3600)
    return _jwks


try:  # pre-warm the JWKS cache at import so the first request doesn't pay the fetch
    if _CLERK_ISS:
        _jwks_client().get_signing_keys()
except Exception:
    pass


_role_cache: Dict[str, tuple] = {}   # sub -> (role, expiry_epoch)


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
                     # Cloudflare fronts api.clerk.com and 403s (err 1010) the default
                     # Python-urllib UA, so set an explicit one or every role lookup fails.
                     "User-Agent": "mcplookup/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:  # nosec B310 - fixed https Clerk API host
            data = json.loads(r.read().decode())
        cand = ((data.get("public_metadata") or {}).get("role") or "").strip().lower()
        if cand in ("admin", "insider", "public"):
            role = cand
    except Exception as exc:  # fail-closed to public, but surface the reason in logs
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
    # Prefer a role claim embedded in the session token (zero network). Falls back to the
    # Clerk API only if the instance session token doesn't carry one yet.
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
    """admins + insiders see risk tiers in lists; public must spend a lookup to reveal."""
    return principal.role in ("admin", "insider")


def charge_lookup(db: Session, principal: Principal, n: int = 1) -> None:
    """Count a lookup (verdict open / search) for public users; enforce the daily cap."""
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


@router.get("/me")
def me(principal: Principal = Depends(get_principal),
       db: Session = Depends(get_session)) -> dict:
    """Current principal + remaining daily lookups (drives the header counter)."""
    unlimited = principal.role in ("admin", "insider")
    used = db.execute(text("SELECT lookups FROM api_usage WHERE user_id=:u AND day=:d"),
                      {"u": principal.user_id, "d": date.today()}).scalar() or 0
    return {"role": principal.role, "unlimited": unlimited, "cap": LOOKUP_CAP,
            "used": used, "remaining": (None if unlimited else max(0, LOOKUP_CAP - used))}


class AxisScore(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None


class Verdict(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    registry_source: Optional[str] = None
    description: Optional[str] = None
    status: str = "scored"                         # "scored" | "unassessed"
    scored_at: Optional[str] = None
    model_version: Optional[str] = None
    axes: Dict[str, AxisScore]
    model_overall_risk: Optional[str] = None       # raw model overall_risk label
    published_overall_risk: Optional[str] = None    # after trust_gating_override (capped)
    trusted_override: bool = False                   # trust_gate applied an override
    override_reason: Optional[str] = None           # basis string when override applied
    trusted: bool = False
    trust_basis: Optional[str] = None
    masquerade_flag: bool = False
    display_label: str = "Automated heuristic assessment"


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/verdict/{server_id}", response_model=Verdict)
def get_verdict(server_id: str, db: Session = Depends(get_session),
                principal: Principal = Depends(get_principal)) -> Verdict:
    """Per-server verdict = its 7 axis rows for the latest model_version, with the
    trust-gating override applied to the published overall_risk. If a server exists
    in mcp_server_registry without scores yet, return an explicit unassessed payload
    instead of HTTP 404."""
    mv = _latest_model_version(db, server_id)
    reg = db.get(McpServerRegistry, server_id)
    if mv is None:
        if reg is None:
            raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")
        return Verdict(
            server_id=server_id,
            name=reg.name,
            url=reg.url,
            registry_source=reg.registry_source,
            description=reg.description,
            status="unassessed",
            scored_at=(reg.last_assessed.isoformat() if reg.last_assessed else None),
            model_version=None,
            axes={},
            model_overall_risk=None,
            published_overall_risk="UNASSESSED",
            trusted_override=False,
            override_reason=None,
            trusted=False,
            trust_basis=None,
            masquerade_flag=False,
            display_label="Queued for automated 7-axis assessment",
        )
    charge_lookup(db, principal)   # opening a server's breakdown counts as a lookup

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    name = reg.name if reg else None
    url = reg.url if reg else None
    scored_ts = max((r.scored_at for r in rows if r.scored_at), default=None)
    scored_at = scored_ts.isoformat() if scored_ts else (
        reg.last_assessed.isoformat() if reg and reg.last_assessed else None
    )

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
    # An override was applied when trust_gate changed the published tier (trusted cap)
    # or flagged a possible masquerade. When trusted=False with no masquerade, no override.
    trusted_override = bool(gate.get("trusted")) or bool(gate.get("masquerade_flag"))
    override_reason = trust_basis if trusted_override else None

    if trusted_override:
        # GR-29 (2026-11-27): this was a fire-and-forget POST of an audit_log INSERT to
        # the factory write_service on 127.0.0.1:8772 -- not in the Fly image, so every
        # trust-gating override went unrecorded behind a bare `except: pass`. Postgres
        # has no audit_log table; the override is now a structured log line, which Fly
        # keeps, instead of a write to a plane this process cannot reach.
        try:
            _audit_log.info("trust_gating %s", json.dumps({
                "event_type": "trust_gating",
                "actor": "verdict_breakdown_api",
                "action": "masquerade_flagged" if masquerade_flag else "trusted_cap_applied",
                "target_server_id": server_id,
                "outcome": "applied",
                "url": url,
                "name": name,
                "verified_basis": bool(trust_basis),  # == gate["trusted"]
                "trust_basis": trust_basis,
                "masquerade_flag": masquerade_flag,
                "model_overall_risk": (gate.get("original_overall_risk")
                                       or labels.get("overall_risk")),
                "published_overall_risk": (gate.get("published_overall_risk")
                                           or labels.get("overall_risk")),
            }, default=str))
        except Exception:
            pass  # never fail the request on an audit-log failure

    return Verdict(
        server_id=server_id,
        name=name,
        url=url,
        registry_source=(reg.registry_source if reg else None),
        description=(reg.description if reg else None),
        status="scored",
        scored_at=scored_at,
        model_version=mv,
        axes=axes,
        model_overall_risk=gate.get("original_overall_risk") or labels.get("overall_risk"),
        published_overall_risk=gate.get("published_overall_risk") or labels.get("overall_risk"),
        trusted_override=trusted_override,
        override_reason=override_reason,
        trusted=trusted,
        trust_basis=trust_basis,
        masquerade_flag=masquerade_flag,
        display_label=gate.get("display_label", "Automated heuristic assessment"),
    )


class SubmitMCP(BaseModel):
    url: str
    name: Optional[str] = None


def _hits_batch(db: Session, rows, reveal: bool = True) -> list[dict]:
    """Batch registry rows -> search-result dicts in O(1) queries (eliminates N+1)."""
    if not rows:
        return []
    if not reveal:
        return [{"server_id": r.server_id, "name": r.name, "url": r.url,
                 "registry_source": r.registry_source,
                 "published_overall_risk": None, "trusted": None, "hidden": True}
                for r in rows]
    sids = [r.server_id for r in rows]
    score_rows = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
        )
        .where(
            McpLlmAxisScore.server_id.in_(sids),
            McpLlmAxisScore.axis_name.in_(("overall_risk", "maintainer_trust")),
        )
        .order_by(McpLlmAxisScore.scored_at.asc().nulls_first(), McpLlmAxisScore.id.asc())
    ).all()
    by_sid: Dict[str, Dict[str, Optional[str]]] = {}
    for sid, axis_name, label in score_rows:
        by_sid.setdefault(sid, {})[axis_name] = label
    out = []
    for r in rows:
        lab = by_sid.get(r.server_id, {})
        gate = trust_gate(r.url, r.name, {k: lab.get(k) for k in lab})
        out.append({
            "server_id": r.server_id, "name": r.name, "url": r.url,
            "registry_source": r.registry_source,
            "published_overall_risk": gate.get("published_overall_risk") or lab.get("overall_risk"),
            "trusted": bool(gate.get("trusted")), "hidden": False,
        })
    return out


def _hit(db: Session, r, reveal: bool = True) -> dict:
    """Registry row -> search-result dict. For public users (reveal=False) the risk
    tier is withheld -> they must open the detail (spend a lookup) to see it."""
    return _hits_batch(db, [r], reveal=reveal)[0]


@router.get("/servers")
def search_servers(q: str = "", risk: str = "", source: str = "",
                   limit: int = 30, offset: int = 0,
                   db: Session = Depends(get_session),
                   principal: Principal = Depends(get_principal)) -> dict:
    """Wildcard search (name/url/id) with optional risk-tier + source filters + pagination."""
    charge_lookup(db, principal)   # a search counts as a lookup
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
    if risk.strip() and reveal:   # public can't enumerate by risk tier (preserves mystery)
        want = risk.strip().upper()
        conds.append(
            exists(
                select(McpLlmAxisScore.id).where(
                    and_(
                        McpLlmAxisScore.server_id == McpServerRegistry.server_id,
                        McpLlmAxisScore.axis_name == "overall_risk",
                        McpLlmAxisScore.label == want,
                    )
                )
            )
        )
    if not q.strip():   # default browse: hide blank/1-char junk-named rows from the first impression
        conds.append(McpServerRegistry.name.isnot(None))
        conds.append(func.length(func.trim(McpServerRegistry.name)) >= 2)
    stmt = select(McpServerRegistry)
    if conds:
        stmt = stmt.where(and_(*conds))
    # PK-ordered early termination: avoids sorting 80k+ matching rows in memory on broad queries.
    stmt = stmt.order_by(McpServerRegistry.server_id).offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()
    hits = _hits_batch(db, rows, reveal)
    if risk.strip() and reveal:   # keep results consistent with badges (published tier)
        want = risk.strip().upper()
        hits = [h for h in hits if (h.get("published_overall_risk") or "").upper() == want]
    return {"servers": hits, "count": len(hits),
            "offset": offset, "limit": limit, "reveal": reveal}


def _compute_summary(db: Session) -> dict:
    """Live aggregate (SLOW on the tiny Fly PG -- only used to (re)build the cache)."""
    scored = db.execute(select(func.count()).where(McpLlmAxisScore.axis_name == "overall_risk")).scalar() or 0
    registry_total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    dist = {(l or "?"): c for l, c in db.execute(
        select(McpLlmAxisScore.label, func.count()).where(
            McpLlmAxisScore.axis_name == "overall_risk").group_by(McpLlmAxisScore.label)).all()}
    by_source = [{"source": s or "unknown", "count": c} for s, c in db.execute(
        select(McpServerRegistry.registry_source, func.count()).group_by(
            McpServerRegistry.registry_source).order_by(func.count().desc()).limit(8)).all()]
    return {"scored": scored, "registry_total": registry_total,
            "risk_distribution": dist, "by_source": by_source}


import threading as _threading
_SUMMARY_LOCK = _threading.Lock()

def _bg_refresh_summary() -> None:
    if not _SUMMARY_LOCK.acquire(blocking=False):
        return
    try:
        from app.db import SessionLocal
        s = SessionLocal()
        try:
            summary = _compute_summary(s)
            s.execute(text(
                "INSERT INTO app_stats(key, value, updated_at) VALUES ('dashboard_summary', CAST(:v AS jsonb), now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                {"v": json.dumps(summary)})
            s.commit()
        finally:
            s.close()
    finally:
        _SUMMARY_LOCK.release()


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_session),
                      principal: Principal = Depends(get_principal)) -> dict:
    """Overview stats (aggregate only - no per-server reveal, so not charged).
    Served from the precomputed app_stats row (full-table aggregates take ~40s on
    the Fly PG tier, so they are cached and refreshed after each scoring run)."""
    try:
        row = db.execute(text("SELECT value FROM app_stats WHERE key = 'dashboard_summary'")).first()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception:
        pass
    # cold cache: never block on the ~40s aggregate -- warm in background, return cheap counts now
    import threading as _th
    _th.Thread(target=_bg_refresh_summary, daemon=True).start()
    try:
        scored = db.execute(select(func.count()).where(McpLlmAxisScore.axis_name == "overall_risk")).scalar() or 0
        registry_total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    except Exception:
        scored = registry_total = 0
    return {"scored": scored, "registry_total": registry_total, "risk_distribution": {}, "by_source": [], "warming": True}


@router.post("/dashboard/refresh")
def refresh_summary(db: Session = Depends(get_session),
                    principal: Principal = Depends(require_admin)) -> dict:
    """Recompute + cache the dashboard summary (admin only; call after a scoring pass)."""
    summary = _compute_summary(db)
    db.execute(text(
        "INSERT INTO app_stats(key, value, updated_at) VALUES ('dashboard_summary', CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"v": json.dumps(summary)})
    db.commit()
    return {"status": "refreshed", **summary}


@router.get("/top")
def top_servers(risk: str = "CRITICAL", limit: int = 24,
                db: Session = Depends(get_session),
                principal: Principal = Depends(get_principal)) -> dict:
    """Curated list at a risk tier for the dashboard. Locked for public users -- the
    list itself would reveal which servers are flagged -- so only insiders/admins see it."""
    if not _reveal(principal):
        return {"servers": [], "risk": risk.strip().upper(), "locked": True}
    want = risk.strip().upper()
    limit = max(1, min(limit, 100))
    # Filter on PUBLISHED (post-trust-gate) tier so the list agrees with the badges.
    sub = select(McpLlmAxisScore.server_id).where(
        McpLlmAxisScore.axis_name == "overall_risk",
        McpLlmAxisScore.label == want).limit(limit * 8)
    cands = db.execute(select(McpServerRegistry).where(
        McpServerRegistry.server_id.in_(sub)).limit(limit * 8)).scalars().all()
    out = []
    for h in _hits_batch(db, cands, True):
        if (h.get("published_overall_risk") or "").upper() == want:
            out.append(h)
            if len(out) >= limit:
                break
    return {"servers": out, "risk": want, "locked": False}


@router.get("/admin/submissions")
def admin_submissions(limit: int = 100, db: Session = Depends(get_session),
                      principal: Principal = Depends(require_admin)) -> dict:
    """User-submitted MCPs awaiting review (registry_source='user_submission')."""
    limit = max(1, min(limit, 500))
    rows = db.execute(select(McpServerRegistry).where(
        McpServerRegistry.registry_source == "user_submission").limit(limit)).scalars().all()
    pending = sum(1 for r in rows if (r.verdict or "") == "unreviewed")
    return {"count": len(rows), "pending": pending,
            "submissions": [{"server_id": r.server_id, "name": r.name, "url": r.url,
                             "verdict": r.verdict or "unreviewed"} for r in rows]}


@router.post("/submit")
def submit_mcp(payload: SubmitMCP, db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal)) -> dict:
    """Add a new MCP to the registry (auth required; verdict=unreviewed -> scorer picks it up)."""
    url = (payload.url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="A valid http(s) URL is required")
    sid = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()  # nosec B324 - non-security id hash
    if db.get(McpServerRegistry, sid):
        return {"status": "exists", "server_id": sid}
    db.add(McpServerRegistry(server_id=sid, name=(payload.name or url)[:512], url=url,
                             registry_source="user_submission", verdict="unreviewed"))
    db.commit()
    return {"status": "submitted", "server_id": sid}


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
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
                            url="https://github.com/stripe/agent-toolkit",
                            registry_source="github"))
    s.add(McpServerRegistry(server_id="srv_unassessed", name="Pending MCP",
                            url="https://github.com/example/pending-mcp",
                            registry_source="github", risk_tier="unassessed"))
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
    r = c.get("/api/verdict/srv1"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "scored", j
    assert j["model_overall_risk"] == "HIGH", j
    assert j["published_overall_risk"] == "MEDIUM", j   # Stripe = verified -> capped
    assert j["trusted"] is True, j
    assert len(j["axes"]) == 7, j

    ru = c.get("/api/verdict/srv_unassessed"); assert ru.status_code == 200, ru.text
    ju = ru.json()
    assert ju["status"] == "unassessed" and ju["published_overall_risk"] == "UNASSESSED", ju
    assert c.get("/api/verdict/nope").status_code == 404
    print("PASS")
