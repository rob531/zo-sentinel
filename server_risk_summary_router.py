"""server_risk_summary_router.py -- Risk summary endpoint for MCP servers.

Exposes GET /servers/{server_id}/risk-summary that returns axis probabilities,
overall risk, risk tier, and last assessment timestamp.

Imports the REAL app data layer (app.db / app.models) -- no inline stubs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisProbability(BaseModel):
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class RiskSummary(BaseModel):
    server_id: str
    axes: Dict[str, AxisProbability]
    overall_risk: float
    risk_tier: str
    last_assessed: Optional[str] = None


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    """Get the most recent model version for a server's axis scores."""
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/{server_id}/risk-summary", response_model=RiskSummary)
def get_risk_summary(server_id: str, db: Session = Depends(get_session)) -> RiskSummary:
    """Return a risk summary for a given MCP server.
    
    Reads from mcp_llm_axis_scores (axis probabilities) and mcp_server_registry
    (risk_tier from registry). Applies trust-gating so official publishers are
    not shown as false HIGH/CRITICAL.
    """
    # Get the latest model version for this server
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    # Fetch all 7 axis rows for the latest model version
    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    # Get registry metadata for trust gating and risk tier
    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    registry_risk_tier = reg.risk_tier if reg else None

    # Build axes mapping and find max p_critical for risk tier determination
    axes: Dict[str, AxisProbability] = {}
    labels: Dict[str, str] = {}
    max_p_critical = 0.0
    max_scored_at: Optional[datetime] = None

    for r in rows:
        axes[r.axis_name] = AxisProbability(
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
        )
        if r.label:
            labels[r.axis_name] = r.label
        if r.p_critical is not None and r.p_critical > max_p_critical:
            max_p_critical = r.p_critical
        if r.scored_at is not None:
            if max_scored_at is None or r.scored_at > max_scored_at:
                max_scored_at = r.scored_at

    # Calculate overall_risk as average of p_critical across all axes
    overall_risk = sum(
        r.p_critical for r in rows if r.p_critical is not None
    ) / len([r for r in rows if r.p_critical is not None]) if rows else 0.0

    # Determine risk tier: CRITICAL if any p_critical > 0.5, else use registry value
    if max_p_critical > 0.5:
        risk_tier = "CRITICAL"
    elif registry_risk_tier:
        risk_tier = registry_risk_tier
    else:
        # Fallback: derive from labels
        overall_label = labels.get("overall_risk", "")
        if overall_label:
            risk_tier = overall_label
        else:
            risk_tier = "UNKNOWN"

    # Apply trust gating (official publishers capped to MEDIUM)
    gate = trust_gate(url, name, labels)
    if gate.get("trusted"):
        # Override risk tier for trusted publishers
        published_risk = gate.get("published_overall_risk")
        if published_risk and published_risk != labels.get("overall_risk"):
            risk_tier = published_risk

    # Format last_assessed as ISO timestamp
    last_assessed = max_scored_at.isoformat() if max_scored_at else None

    return RiskSummary(
        server_id=server_id,
        axes=axes,
        overall_risk=round(overall_risk, 4),
        risk_tier=risk_tier,
        last_assessed=last_assessed,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Create in-memory SQLite for self-test
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed test data using ONLY required constructor kwargs for McpLlmAxisScore
    # REQUIRED: id, server_id, axis_name, label, model_version
    # (do NOT pass columns with DB defaults - they would become NULL and violate NOT NULL)
    s = TS()
    s.add(McpServerRegistry(server_id="test-server", name="Stripe MCP",
                           url="https://github.com/stripe/agent-toolkit",
                           risk_tier="LOW"))

    # All 7 axes for test-server (with p_critical so overall_risk computes without div/0)
    for _i, (ax, lbl, pc) in enumerate((
        ("overall_risk", "HIGH", 0.3),
        ("auth_strength", "STRONG", 0.1),
        ("capability_breadth", "BROAD", 0.2),
        ("data_sensitivity", "CRITICAL", 0.4),
        ("network_egress", "EXTERNAL", 0.2),
        ("maintainer_trust", "ESTABLISHED", 0.1),
        ("exploit_surface", "MODERATE", 0.2),
    ), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="test-server", axis_name=ax,
                              label=lbl, model_version="v3.0_40974559",
                              p_critical=pc, p_top=0.9, p_danger=0.3))

    # A server with p_critical > 0.5 to trigger CRITICAL tier
    s.add(McpServerRegistry(server_id="evil-server", name="Malicious MCP",
                           url="https://evil.example.com", risk_tier="LOW"))
    for _i, (ax, lbl, pc) in enumerate((
        ("overall_risk", "CRITICAL", 0.8),
        ("auth_strength", "WEAK", 0.3),
        ("capability_breadth", "BROAD", 0.4),
        ("data_sensitivity", "CRITICAL", 0.7),
        ("network_egress", "EXTERNAL", 0.6),
        ("maintainer_trust", "UNKNOWN", 0.9),
        ("exploit_surface", "HIGH", 0.55),
    ), start=100):
        s.add(McpLlmAxisScore(id=_i, server_id="evil-server", axis_name=ax,
                              label=lbl, model_version="v3.0_40974559",
                              p_critical=pc, p_top=0.9, p_danger=0.5))

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
    c = TestClient(app)

    # Test 1: Happy path - Stripe (trusted publisher, capped to MEDIUM)
    r = c.get("/api/servers/test-server/risk-summary")
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    assert "overall_risk" in j["axes"], "Missing overall_risk axis"
    assert all(ax in j["axes"] for ax in AXES), f"Missing axes, got: {list(j['axes'].keys())}"
    # Stripe is trusted -> risk_tier capped to MEDIUM (not HIGH)
    assert j["risk_tier"] == "MEDIUM", f"Expected MEDIUM for trusted publisher, got {j['risk_tier']}"
    assert j["overall_risk"] >= 0.0 and j["overall_risk"] <= 1.0

    # Test 2: CRITICAL tier when p_critical > 0.5
    r2 = c.get("/api/servers/evil-server/risk-summary")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["risk_tier"] == "CRITICAL", f"Expected CRITICAL when p_critical > 0.5, got {j2['risk_tier']}"
    assert len(j2["axes"]) == 7, f"Expected 7 axes, got {len(j2['axes'])}"

    # Test 3: 404 for unknown server
    r3 = c.get("/api/servers/nonexistent/risk-summary")
    assert r3.status_code == 404, f"Expected 404 for unknown server, got {r3.status_code}"

    print("PASS")
