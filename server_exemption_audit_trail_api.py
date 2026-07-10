# deps: fastapi, pydantic, sqlalchemy
"""server_exemption_audit_trail_api.py -- exemption/dispute audit trail per server.

Reads from mcp_score_disputes and mcp_exemptions via the real app data layer
(app.db / app.models). Produces a chronological event log for a given server_id.

Mounted by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional

import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, or_, and_, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpExemption

router = APIRouter(prefix="/api", tags=["exemption-audit"])

# ===================== Clerk auth ===========================================
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


# ===================== Response models ======================================

class AuditEvent(BaseModel):
    event_type: str          # dispute_submitted | dispute_resolved | exemption_granted | exemption_expired | exemption_revoked
    ts: str                  # ISO 8601
    actor: Optional[str]
    detail: dict


class AuditTrailResponse(BaseModel):
    server_id: str
    events: List[AuditEvent]
    total: int


class DisputeDetailResponse(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Optional[dict]
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: str
    resolved_at: Optional[str]


# ===================== Endpoints ============================================

@router.get("/servers/{server_id}/exemption-audit", response_model=AuditTrailResponse)
def get_exemption_audit(
    server_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> AuditTrailResponse:
    """Chronological audit trail of exemption/dispute decisions for a server.
    Events ordered newest-first. Combines dispute submissions, resolutions,
    exemptions granted, expirations, and revocations."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    events: List[AuditEvent] = []

    # Dispute events
    disputes = db.execute(
        select(McpScoreDispute)
        .where(McpScoreDispute.server_id == server_id)
        .order_by(McpScoreDispute.created_at.desc())
    ).scalars().all()

    for d in disputes:
        # dispute_submitted
        events.append(AuditEvent(
            event_type="dispute_submitted",
            ts=_iso(d.created_at),
            actor=d.submitted_by,
            detail={
                "dispute_id": d.id,
                "proposed_overall_risk": d.proposed_overall_risk,
                "reason_category": d.reason_category,
                "explanation": d.explanation,
                "status": d.status,
            },
        ))
        # dispute_resolved (only when resolved_at is set)
        if d.resolved_at is not None:
            events.append(AuditEvent(
                event_type="dispute_resolved",
                ts=_iso(d.resolved_at),
                actor=None,
                detail={
                    "dispute_id": d.id,
                    "status": d.status,
                    "admin_note": d.admin_note,
                    "proposed_overall_risk": d.proposed_overall_risk,
                },
            ))

    # Exemption events
    exemptions = db.execute(
        select(McpExemption)
        .where(McpExemption.server_id == server_id)
        .order_by(McpExemption.created_at.desc())
    ).scalars().all()

    for e in exemptions:
        conditions = None
        if e.conditions_json:
            try:
                conditions = json.loads(e.conditions_json)
            except Exception:
                conditions = e.conditions_json

        # exemption_granted
        events.append(AuditEvent(
            event_type="exemption_granted",
            ts=_iso(e.created_at),
            actor=e.granted_by,
            detail={
                "exemption_id": e.exemption_id,
                "reason": e.reason,
                "conditions": conditions,
                "expires_at": _iso(e.expires_at) if e.expires_at else None,
                "active": e.active,
            },
        ))

        # exemption_expired / exemption_revoked: only if expired and inactive
        # (best-effort detection: created_at is before now, active is False, expires_at is set)
        if e.expires_at is not None and not e.active:
            events.append(AuditEvent(
                event_type="exemption_expired",
                ts=_iso(e.expires_at),
                actor=None,
                detail={
                    "exemption_id": e.exemption_id,
                    "reason": e.reason,
                },
            ))
        # exemption_revoked: active=False but we don't have an explicit revoked_at -- skip;
        # a future schema addition would surface this as a distinct event.

    # Sort all events newest-first
    events.sort(key=lambda e: e.ts, reverse=True)
    total = len(events)

    return AuditTrailResponse(
        server_id=server_id,
        events=events[offset : offset + limit],
        total=total,
    )


@router.get("/servers/{server_id}/dispute/{dispute_id}", response_model=DisputeDetailResponse)
def get_dispute_detail(
    server_id: str,
    dispute_id: int,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> DisputeDetailResponse:
    """Full dispute record for a given server + dispute id."""
    row = db.execute(
        select(McpScoreDispute).where(
            McpScoreDispute.id == dispute_id,
            McpScoreDispute.server_id == server_id,
        )
    ).scalars().first()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Dispute {dispute_id} not found for server {server_id!r}")

    return DisputeDetailResponse(
        id=row.id,
        server_id=row.server_id,
        submitted_by=row.submitted_by,
        proposed_overall_risk=row.proposed_overall_risk,
        proposed_axes=row.proposed_axes,
        reason_category=row.reason_category,
        explanation=row.explanation,
        status=row.status,
        admin_note=row.admin_note,
        created_at=_iso(row.created_at),
        resolved_at=_iso(row.resolved_at),
    )


def _iso(dt: Optional[datetime]) -> str:
    """Format datetime to ISO 8601 string."""
    if dt is None:
        return ""
    return dt.isoformat()


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

    # Seed: one server, two disputes (pending + resolved), one active exemption
    s = TS()
    s.add(McpScoreDispute(
        id=1, server_id="test-server-1", submitted_by="user_alice",
        proposed_overall_risk="MEDIUM", proposed_axes={"overall_risk": "MEDIUM"},
        reason_category="false_positive", explanation="Stripe is verified.",
        status="pending"))
    s.add(McpScoreDispute(
        id=2, server_id="test-server-1", submitted_by="user_bob",
        proposed_overall_risk="LOW", proposed_axes={"overall_risk": "LOW"},
        reason_category="corrected_label", explanation="Updated label.",
        status="approved", admin_note="Approved after review.",
        resolved_at=datetime(2026, 6, 15, 12, 0, 0)))
    s.add(McpExemption(
        exemption_id="EX001", server_id="test-server-1",
        reason="Security review completed",
        granted_by="admin_charlie",
        conditions_json='{"monitoring": true, "review_frequency": "monthly"}',
        expires_at=datetime(2026, 12, 31, 23, 59, 59),
        active=True,
        created_at=datetime(2026, 6, 1, 0, 0, 0)))
    # Inactive/expiry exemption to exercise exemption_expired event
    s.add(McpExemption(
        exemption_id="EX002", server_id="test-server-1",
        reason="Temporary exception",
        granted_by="admin_dana",
        expires_at=datetime(2026, 1, 1, 0, 0, 0),
        active=False,
        created_at=datetime(2025, 12, 1, 0, 0, 0)))
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

    # Happy path: audit trail
    r = c.get("/api/servers/test-server-1/exemption-audit")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "test-server-1", j
    assert "events" in j, j
    assert j["total"] == 6, j   # dispute_submitted×2 + dispute_resolved + exemption_granted + exemption_expired + exemption_granted(inactive)
    event_types = {e["event_type"] for e in j["events"]}
    assert "dispute_submitted" in event_types, event_types
    assert "exemption_granted" in event_types, event_types
    # pagination: limit=1
    r2 = c.get("/api/servers/test-server-1/exemption-audit?limit=1&offset=0")
    assert r2.status_code == 200
    assert len(r2.json()["events"]) == 1

    # Happy path: dispute detail
    r3 = c.get("/api/servers/test-server-1/dispute/1")
    assert r3.status_code == 200, r3.text
    d = r3.json()
    assert d["id"] == 1, d
    assert d["submitted_by"] == "user_alice", d
    assert d["proposed_overall_risk"] == "MEDIUM", d
    assert d["reason_category"] == "false_positive", d
    assert d["status"] == "pending", d
    assert d["resolved_at"] == "", d  # pending -> null resolved_at -> ""

    # Dispute 2: resolved
    r4 = c.get("/api/servers/test-server-1/dispute/2")
    assert r4.status_code == 200
    d2 = r4.json()
    assert d2["status"] == "approved", d2
    assert d2["admin_note"] == "Approved after review.", d2
    assert d2["resolved_at"] != "", d2

    # 404: dispute not found
    r5 = c.get("/api/servers/test-server-1/dispute/999")
    assert r5.status_code == 404, r5.text

    # 404: server with no records
    r6 = c.get("/api/servers/nonexistent/exemption-audit")
    assert r6.status_code == 200   # returns empty events, not 404
    assert r6.json()["total"] == 0, r6.json()

    print("PASS")
