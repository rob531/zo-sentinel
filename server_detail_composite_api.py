"""server_detail_composite_api.py -- Composite server detail + risk summary endpoint.

GET /servers/{server_id}/detail-summary
Reads: risk axes from mcp_llm_axis_scores, vuln data via write_service from
vuln_links + vuln_advisories, server row from mcp_server_registry.
Applies trust_gating_override so official publishers show capped (not false HIGH/CRITICAL).

Mounted by app.main via _OPTIONAL_ROUTERS.
"""
from __future__ import annotations

import json
import requests
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["servers"])


def _ws_query(sql: str, params: Optional[dict] = None) -> List[dict]:
    """Read from write_service (ZoComputer mesh/pipeline tables + live app tables)."""
    try:
        r = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": list(params.values()) if params else []},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("rows", [])
    except Exception:
        return []


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


# Pydantic models -----------------------------------------------------------

class AxisSummary(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Optional[dict] = None


class VulnSummary(BaseModel):
    advisory_id: str
    feed: Optional[str] = None
    severity: Optional[str] = None
    summary: Optional[str] = None
    source_url: Optional[str] = None


class ServerDetailSummary(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    scan_count: Optional[int] = None
    last_scanned: Optional[str] = None
    axes: List[AxisSummary]
    top_vulns: List[VulnSummary]
    criteria_version: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[str] = None


# Auth -----------------------------------------------------------------------

class Principal(BaseModel):
    user_id: str
    role: str = "public"


_bearer = HTTPBearer(auto_error=False)


def get_principal(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Principal:
    """Stub principal for this module -- caller may override via dependency_overrides."""
    if creds is None:
        return Principal(user_id="anon", role="public")
    return Principal(user_id="authed", role="public")


# Endpoints -----------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}


@router.get("/servers/{server_id}/detail-summary", response_model=ServerDetailSummary)
def get_server_detail_summary(
    server_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ServerDetailSummary:
    """
    Composite detail: 6 risk axes (sorted by p_top desc), top 5 vulns by severity,
    and server registry metadata for a single server.
    """
    # --- axes + metadata from app Postgres ---
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    axis_rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    verdict = reg.verdict if reg else None
    risk_tier = reg.risk_tier if reg else None
    scan_count = reg.scan_count if reg else None
    last_scanned = reg.last_scanned.isoformat() if reg and reg.last_scanned else None

    # Build axes dict and collect labels for trust gating
    axes_map: Dict[str, AxisSummary] = {}
    labels: Dict[str, str] = {}
    decision_rule_version: Optional[str] = None
    scored_at: Optional[str] = None

    for r in axis_rows:
        axes_map[r.axis_name] = AxisSummary(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            probs=r.probs,
        )
        if r.label:
            labels[r.axis_name] = r.label
        if r.decision_rule_version:
            decision_rule_version = r.decision_rule_version
        if r.scored_at:
            scored_at = r.scored_at.isoformat()

    # Apply trust gating to the published risk_tier
    gate = trust_gate(url, name, labels)
    published_risk = gate.get("published_overall_risk") or labels.get("overall_risk")
    if gate.get("trusted") and risk_tier in ("HIGH", "CRITICAL"):
        # Override registry risk_tier with the capped trust-gated value
        if published_risk in ("LOW", "MEDIUM"):
            risk_tier = published_risk

    # Sort axes by p_top descending, None last
    sorted_axes = sorted(
        axes_map.values(),
        key=lambda a: (a.p_top is None, -(a.p_top or 0)),
    )

    # --- vuln data from write_service ---
    vuln_rows = _ws_query(
        """
        SELECT va.advisory_id, va.feed, va.severity, va.summary, va.source_url
        FROM vuln_links vl
        JOIN vuln_advisories va ON va.id = vl.advisory_id
        WHERE vl.server_id = ?
        ORDER BY
            CASE va.severity
                WHEN 'CRITICAL' THEN 0
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 3
                ELSE 4
            END,
            vl.linked_at DESC
        LIMIT 5
        """,
        params={"server_id": server_id},
    )

    top_vulns = [
        VulnSummary(
            advisory_id=row.get("advisory_id", ""),
            feed=row.get("feed"),
            severity=row.get("severity"),
            summary=row.get("summary"),
            source_url=row.get("source_url"),
        )
        for row in vuln_rows
    ]

    return ServerDetailSummary(
        server_id=server_id,
        name=name,
        verdict=verdict,
        risk_tier=risk_tier,
        scan_count=scan_count,
        last_scanned=last_scanned,
        axes=sorted_axes,
        top_vulns=top_vulns,
        criteria_version=decision_rule_version,
        model_version=mv,
        scored_at=scored_at,
    )


if __name__ == "__main__":
    # Self-test: seed SQLite with axis + server data, mock write_service for vulns
    from unittest.mock import patch
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
        server_id="srv1",
        name="TestCorp MCP",
        url="https://github.com/testcorp/mcp",
        verdict="scored",
        risk_tier="HIGH",
        scan_count=3,
    ))
    # Seed 7 axes (including overall_risk)
    for _i, (ax, lbl) in enumerate((
        ("overall_risk", "HIGH"),
        ("auth_strength", "STRONG"),
        ("capability_breadth", "BROAD"),
        ("data_sensitivity", "CRITICAL"),
        ("network_egress", "EXTERNAL"),
        ("maintainer_trust", "ESTABLISHED"),
        ("exploit_surface", "MODERATE"),
    ), start=1):
        s.add(McpLlmAxisScore(
            id=_i,
            server_id="srv1",
            axis_name=ax,
            label=lbl,
            model_version="v3.0_40974559",
        ))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")

    mock_vuln_response = {
        "rows": [
            {
                "advisory_id": "CVE-2025-1001",
                "feed": "nvd",
                "severity": "CRITICAL",
                "summary": "Remote code execution in testcorp-mcp",
                "source_url": "https://nvd.nist.gov/vuln/detail/CVE-2025-1001",
            },
            {
                "advisory_id": "GHSA-abcd-1234",
                "feed": "ghsa",
                "severity": "HIGH",
                "summary": "Privilege escalation via malicious plugin",
                "source_url": "https://github.com/advisories/GHSA-abcd-1234",
            },
        ]
    }

    def _mock_post(url, json, timeout=None):
        class R:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return mock_vuln_response
        return R()

    c = TestClient(app)
    with patch("requests.post", side_effect=_mock_post):
        r = c.get("/api/servers/srv1/detail-summary")

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()

    # Verify all required keys present
    for key in ("server_id", "name", "verdict", "risk_tier", "scan_count",
                "last_scanned", "axes", "top_vulns",
                "criteria_version", "model_version", "scored_at"):
        assert key in j, f"Missing key: {key}"

    # axes must be non-empty and sorted by p_top desc (none are None here)
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    p_tops = [a["p_top"] for a in j["axes"] if a["p_top"] is not None]
    assert p_tops == sorted(p_tops, reverse=True), f"Axes not sorted by p_top desc: {p_tops}"

    # top_vulns from mocked write_service
    assert len(j["top_vulns"]) == 2, f"Expected 2 vulns, got {len(j['top_vulns'])}"
    assert j["top_vulns"][0]["advisory_id"] == "CVE-2025-1001"
    assert j["top_vulns"][0]["severity"] == "CRITICAL"
    assert j["top_vulns"][1]["severity"] == "HIGH"

    # server metadata
    assert j["server_id"] == "srv1"
    assert j["name"] == "TestCorp MCP"
    assert j["model_version"] == "v3.0_40974559"

    # 404 for nonexistent server
    with patch("requests.post", side_effect=_mock_post):
        r2 = c.get("/api/servers/nonexistent/detail-summary")
    assert r2.status_code == 404, f"Expected 404, got {r2.status_code}"

    print("PASS")
