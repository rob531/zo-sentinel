"""server_risk_report_api.py -- printable self-contained risk report for a single MCP server.

Collates server metadata + 7 risk axes + overall risk + threat intel + CVE exposure
into one JSON blob. Reads from the real app Postgres (app.db / app.models), applies
trust-gating so official publishers are not shown as false HIGH/CRITICAL.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from datetime import datetime, date
from typing import Dict, Optional

import jwt
import requests
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, VulnLink, VulnAdvisory, ThreatIntelRef
from trust_gating_override import trust_gate

WRITE_SERVICE = "http://127.0.0.1:8772"

router = APIRouter(prefix="/api", tags=["risk-report"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

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


# ===================== Pydantic models =====================
class ServerMeta(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    scan_count: Optional[int] = None


class AxisDetail(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Optional[dict] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[datetime] = None


class OverallRisk(BaseModel):
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    risk_tier: Optional[str] = None
    criteria_version: Optional[str] = None


class ThreatIntelEntry(BaseModel):
    indicator_type: str
    indicator_value: str
    source: Optional[str] = None
    pulse_name: Optional[str] = None


class CVEEntry(BaseModel):
    cve_id: str
    severity: Optional[str] = None
    summary: Optional[str] = None
    match_confidence: float


class VerdictInfo(BaseModel):
    verdict: Optional[str] = None
    verdict_reasoning: Optional[str] = None
    confidence: Optional[float] = None


class RiskReportResponse(BaseModel):
    server: ServerMeta
    axes: list[AxisDetail]
    overall: OverallRisk
    threat_intel: list[ThreatIntelEntry]
    cve_exposure: list[CVEEntry]
    verdict: VerdictInfo


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/{server_id}/report", response_model=RiskReportResponse)
def get_server_risk_report(server_id: str,
                            db: Session = Depends(get_session),
                            principal: Principal = Depends(get_principal)) -> RiskReportResponse:
    """Self-contained printable risk report for a single MCP server."""
    charge_lookup(db, principal)

    reg = db.get(McpServerRegistry, server_id)
    if not reg:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    # Server metadata
    server_meta = ServerMeta(
        server_id=reg.server_id,
        name=reg.name,
        registry_source=reg.registry_source,
        url=reg.url,
        description=reg.description,
        first_seen=reg.first_seen,
        last_seen=reg.last_seen,
        scan_count=reg.scan_count,
    )

    # Verdict from registry
    verdict_info = VerdictInfo(
        verdict=reg.verdict,
        verdict_reasoning=reg.verdict_reasoning,
        confidence=reg.confidence,
    )

    # 7 risk axes
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No axis scores for server_id {server_id!r}")

    axes: list[AxisDetail] = []
    labels: Dict[str, str] = {}
    overall_row = None

    for r in rows:
        if r.label:
            labels[r.axis_name] = r.label
        if r.axis_name == "overall_risk":
            overall_row = r
        axes.append(AxisDetail(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            probs=r.probs,
            decision_rule_version=r.decision_rule_version,
            model_version=r.model_version,
            scored_at=r.scored_at,
        ))

    # Apply trust gate
    gate = trust_gate(reg.url, reg.name, labels)
    published_risk = gate.get("published_overall_risk") or labels.get("overall_risk")

    # Overall
    overall = OverallRisk(
        p_top=overall_row.p_top if overall_row else None,
        p_critical=overall_row.p_critical if overall_row else None,
        p_danger=overall_row.p_danger if overall_row else None,
        risk_tier=published_risk,
        criteria_version=overall_row.decision_rule_version if overall_row else None,
    )

    # Threat intel from threat_intel_refs (read via write_service for mesh table)
    threat_intel: list[ThreatIntelEntry] = []
    try:
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json={
                "sql": (
                    "SELECT indicator_type, indicator_value, source, pulse_name "
                    "FROM threat_intel_refs "
                    "WHERE indicator_type IN ('cve','domain') "
                    "AND indicator_value IN ("
                    "  SELECT id FROM vuln_advisories WHERE id IN ("
                    "    SELECT advisory_id FROM vuln_links WHERE server_id = :sid"
                    "  )"
                    ") UNION ALL "
                    "SELECT indicator_type, indicator_value, source, pulse_name "
                    "FROM threat_intel_refs "
                    "WHERE indicator_type = 'domain' "
                    "AND indicator_value IN ("
                    "  SELECT url FROM mcp_server_registry WHERE server_id = :sid"
                    ")"
                ),
                "params": {"sid": server_id},
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            rows_ti = data.get("rows", []) if isinstance(data, dict) else []
            for row_ti in rows_ti:
                threat_intel.append(ThreatIntelEntry(
                    indicator_type=row_ti.get("indicator_type", ""),
                    indicator_value=row_ti.get("indicator_value", ""),
                    source=row_ti.get("source"),
                    pulse_name=row_ti.get("pulse_name"),
                ))
    except Exception:
        pass  # never fail the report on threat intel lookup failure

    # CVE exposure via app Postgres (mcp_threat_associations -> vuln_links/vuln_advisories)
    cve_exposure: list[CVEEntry] = []
    vuln_rows = db.execute(
        select(VulnLink, VulnAdvisory).join(
            VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id
        ).where(VulnLink.server_id == server_id)
    ).all()

    for vl, va in vuln_rows:
        cve_exposure.append(CVEEntry(
            cve_id=va.id,
            severity=va.severity,
            summary=va.summary,
            match_confidence=vl.match_confidence,
        ))

    return RiskReportResponse(
        server=server_meta,
        axes=axes,
        overall=overall,
        threat_intel=threat_intel,
        cve_exposure=cve_exposure,
        verdict=verdict_info,
    )


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
    s.add(McpServerRegistry(
        server_id="srv1", name="Stripe MCP", registry_source="github",
        url="https://github.com/stripe/agent-toolkit",
        description="Official Stripe MCP server",
        first_seen=datetime(2025, 1, 1), last_seen=datetime(2025, 6, 1),
        scan_count=5, verdict="approved", verdict_reasoning="Verified publisher",
        confidence=0.95))
    for _i, (ax, lbl) in enumerate(((
        ("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
        ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
        ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
        ("exploit_surface", "MODERATE")
    )), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559",
                              p_top=0.3, p_critical=0.5, p_danger=0.15,
                              decision_rule_version="v2.1"))
    s.commit()
    s.close()

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

    # 404 for unknown server
    r404 = c.get("/api/servers/unknown-server/report")
    assert r404.status_code == 404, f"Expected 404, got {r404.status_code}: {r404.text}"

    # Happy path
    r = c.get("/api/servers/srv1/report")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()

    # All 7 axes present exactly once
    axis_names = {a["axis_name"] for a in j["axes"]}
    assert axis_names == set(AXES), f"Expected all 7 axes, got {axis_names}"

    # Overall section
    assert "risk_tier" in j["overall"], j
    assert "criteria_version" in j["overall"], j

    # Server metadata
    assert j["server"]["server_id"] == "srv1"
    assert j["server"]["name"] == "Stripe MCP"

    # Verdict
    assert "verdict" in j["verdict"]

    # CVE / threat intel (may be empty in test DB)
    assert isinstance(j["cve_exposure"], list)
    assert isinstance(j["threat_intel"], list)

    print("PASS")
