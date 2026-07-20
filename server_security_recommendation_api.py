"""server_security_recommendation_api.py -- per-server security recommendations.

Reads the 7 risk axes from mcp_llm_axis_scores, classifies each by severity
(CRITICAL/HIGH/MEDIUM/LOW) based on p_top/p_critical, and returns a prioritized
list of actionable security recommendations.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["security"])


AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

AXIS_RECOMMENDATIONS: Dict[str, Dict[str, str]] = {
    "overall_risk": {
        "CRITICAL": "Immediately isolate this server. Conduct a full threat model review before any integration. Treat as actively hostile.",
        "HIGH": "Implement strict network segmentation. Require explicit user confirmation before allowing this server to run. Audit all tool calls.",
        "MEDIUM": "Enable comprehensive request/response logging. Set up alerting for unusual capability invocations.",
        "LOW": "Continue monitoring usage patterns. Periodically re-assess as the server evolves.",
    },
    "auth_strength": {
        "CRITICAL": "This server has no or severely weak authentication. Block production use until OAuth 2.0 / mTLS is implemented.",
        "HIGH": "Authentication is weak or inconsistently applied. Migrate to short-lived tokens with audience restrictions.",
        "MEDIUM": "Review token refresh cadence and scope. Ensure tokens are bound to specific user sessions.",
        "LOW": "Authentication posture is adequate. Keep secrets rotated and audit token issuance policies.",
    },
    "capability_breadth": {
        "CRITICAL": "This server exposes an extremely broad attack surface. Deploy in a sandboxed environment with zero network access except for declared endpoints.",
        "HIGH": "Broad capability set detected. Implement allowlists for file paths, URLs, and commands this server may invoke.",
        "MEDIUM": "Review the full list of tools exposed. Remove or disable any capabilities not required for your use case.",
        "LOW": "Capability set is well-scoped. Periodically audit the tool manifest for drift.",
    },
    "data_sensitivity": {
        "CRITICAL": "This server handles highly sensitive data. Enforce field-level encryption in transit and at rest. Implement data loss prevention controls.",
        "HIGH": "Sensitive data exposure risk detected. Apply output filtering and PII redaction before logging or returning results.",
        "MEDIUM": "Review what data fields this server can access. Minimize the data shared with this server to only what is necessary.",
        "LOW": "Data sensitivity is low. Standard security hygiene is sufficient.",
    },
    "network_egress": {
        "CRITICAL": "This server can make outbound network connections to arbitrary hosts. Block egress at the firewall except for declared destinations.",
        "HIGH": "External network access detected. Enforce DNS-level filtering and TLS inspection on all outbound connections.",
        "MEDIUM": "Review and validate all external endpoints this server communicates with. Maintain an allowlist.",
        "LOW": "Network egress is minimal or controlled. Monitor for unexpected new outbound connections.",
    },
    "maintainer_trust": {
        "CRITICAL": "The maintainer has no established reputation or trust indicators. Do not use in production without a thorough manual security audit.",
        "HIGH": "Maintainer trust is low. Require manual security review and approval before deploying. Monitor for sudden maintainer changes.",
        "MEDIUM": "Maintainer has some history but limited external validation. Subscribe to their security advisories and release notes.",
        "LOW": "Maintainer has a good reputation. Monitor for ownership or maintainer changes that could affect trust.",
    },
    "exploit_surface": {
        "CRITICAL": "This server presents a high exploit surface. Isolate in a hardened sandbox. Disable dynamic code loading and filesystem write access.",
        "HIGH": "Notable attack surface detected. Keep all dependencies patched. Deploy behind a WAF with aggressive rate limiting.",
        "MEDIUM": "Review dependency tree for known vulnerabilities. Schedule regular penetration testing focused on this integration.",
        "LOW": "Exploit surface is minimal. Maintain dependency updates and standard hardening practices.",
    },
}


def _severity(p_top: Optional[float], p_critical: Optional[float]) -> str:
    """Classify axis severity based on p_top and p_critical."""
    if p_critical is not None and p_critical > 0.7:
        return "CRITICAL"
    if p_top is None or p_top < 30:
        return "HIGH"
    if p_top < 60:
        return "MEDIUM"
    return "LOW"


def _recommendation(axis: str, severity: str, label: Optional[str]) -> str:
    """Return the recommendation string for an axis + severity."""
    template = AXIS_RECOMMENDATIONS.get(axis, {}).get(severity)
    if template:
        return template
    return f"Review the {axis} axis (label: {label or 'unknown'}). Risk level: {severity}."


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


class AxisRecommendation(BaseModel):
    axis: str
    label: Optional[str] = None
    severity: str
    recommendation: str


class RecommendationsResponse(BaseModel):
    server_id: str
    verdict: str
    risk_tier: str
    recommendations: List[AxisRecommendation]


@router.get("/servers/{server_id}/recommendations", response_model=RecommendationsResponse)
def get_recommendations(
    server_id: str,
    db: Session = Depends(get_session),
) -> RecommendationsResponse:
    """Return prioritized security recommendations for a server based on its 7 risk axes."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    risk_tier = reg.risk_tier if reg else None
    verdict = reg.verdict if reg else None

    # Build labels dict for trust gate
    labels: Dict[str, str] = {}
    for r in rows:
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)
    published_risk = gate.get("published_overall_risk") or labels.get("overall_risk") or "UNKNOWN"

    # CRITICAL override: if any axis has p_critical > 0.7, that axis is CRITICAL regardless
    recommendations: List[AxisRecommendation] = []
    for axis in AXES:
        match = next((r for r in rows if r.axis_name == axis), None)
        if match:
            label = match.label
            p_top = match.p_top
            p_critical = match.p_critical
        else:
            label = None
            p_top = None
            p_critical = None
        severity = _severity(p_top, p_critical)
        rec_text = _recommendation(axis, severity, label)
        recommendations.append(
            AxisRecommendation(axis=axis, label=label, severity=severity, recommendation=rec_text)
        )

    # Sort: CRITICAL first, then HIGH, MEDIUM, LOW
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    recommendations.sort(key=lambda x: order.get(x.severity, 99))

    return RecommendationsResponse(
        server_id=server_id,
        verdict=verdict or published_risk,
        risk_tier=risk_tier or published_risk,
        recommendations=recommendations,
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

    # Seed server with all 7 axes
    s = TS()
    s.add(McpServerRegistry(
        server_id="srv_known",
        name="Test MCP",
        url="https://github.com/test-org/test-mcp",
        verdict="UNREVIEWED",
        risk_tier="HIGH",
    ))
    # p_top values that exercise all three p_top-based bands + one CRITICAL p_critical
    axis_data = [
        ("overall_risk", "HIGH", 80.0, None),
        ("auth_strength", "STRONG", 10.0, None),          # HIGH (p_top < 30)
        ("capability_breadth", "BROAD", 45.0, None),        # MEDIUM (30 <= p_top < 60)
        ("data_sensitivity", "CRITICAL", 20.0, None),        # HIGH (p_top < 30)
        ("network_egress", "EXTERNAL", 90.0, None),         # LOW (p_top >= 60)
        ("maintainer_trust", "ESTABLISHED", 55.0, None),     # MEDIUM
        ("exploit_surface", "MODERATE", 15.0, 0.75),        # CRITICAL (p_critical > 0.7)
    ]
    for _i, (ax, lbl, pt, pc) in enumerate(axis_data, start=1):
        s.add(McpLlmAxisScore(
            id=_i,
            server_id="srv_known",
            axis_name=ax,
            label=lbl,
            model_version="v3.0_40974559",
            p_top=pt,
            p_critical=pc,
        ))
    # Unknown server: no axes
    s.add(McpServerRegistry(
        server_id="srv_unknown",
        name="Unknown Server",
        url="https://example.com/unknown",
    ))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Test 1: unknown server -> 404
    r = c.get("/api/servers/nope/recommendations")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

    # Test 2: known server -> 200, all 7 axes present
    r = c.get("/api/servers/srv_known/recommendations")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert j["server_id"] == "srv_known"
    assert len(j["recommendations"]) == 7, f"Expected 7 axes, got {len(j['recommendations'])}"

    # Test 3: CRITICAL override exercised (exploit_surface p_critical=0.75 > 0.7)
    exploit = next((rec for rec in j["recommendations"] if rec["axis"] == "exploit_surface"), None)
    assert exploit is not None, "exploit_surface axis missing"
    assert exploit["severity"] == "CRITICAL", f"Expected CRITICAL, got {exploit['severity']}"

    # Test 4: HIGH severity on auth_strength (p_top=10 < 30, no CRITICAL p_critical)
    auth = next((rec for rec in j["recommendations"] if rec["axis"] == "auth_strength"), None)
    assert auth is not None
    assert auth["severity"] == "HIGH", f"Expected HIGH, got {auth['severity']}"

    # Test 5: MEDIUM severity on capability_breadth (30 <= p_top=45 < 60)
    cap = next((rec for rec in j["recommendations"] if rec["axis"] == "capability_breadth"), None)
    assert cap is not None
    assert cap["severity"] == "MEDIUM", f"Expected MEDIUM, got {cap['severity']}"

    # Test 6: LOW severity on network_egress (p_top=90 >= 60)
    net = next((rec for rec in j["recommendations"] if rec["axis"] == "network_egress"), None)
    assert net is not None
    assert net["severity"] == "LOW", f"Expected LOW, got {net['severity']}"

    # Test 7: recommendations are sorted CRITICAL first
    severities = [rec["severity"] for rec in j["recommendations"]]
    assert severities[0] == "CRITICAL", f"First severity should be CRITICAL, got {severities}"

    # Test 8: all recommendations have non-empty strings
    for rec in j["recommendations"]:
        assert rec["axis"], "axis must be non-empty"
        assert rec["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW"), f"Invalid severity: {rec['severity']}"
        assert rec["recommendation"], "recommendation text must be non-empty"

    print("PASS")
