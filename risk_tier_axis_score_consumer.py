# deps: requests
"""FastAPI router exposing GET /risk-tier/{server_id} that consumes the 7 risk axes
from mcp_llm_axis_scores and returns the risk_tier with trust-gating override applied."""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk-tier"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

CRITERIA_VERSION = "v1.0"


class AxisData(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    probs: Optional[Dict] = None


class RiskTierResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisData]
    overall: Optional[str] = None
    risk_tier: Optional[str] = None
    criteria_version: str = CRITERIA_VERSION


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/risk-tier/{server_id}", response_model=RiskTierResponse)
def get_risk_tier(server_id: str, db: Session = Depends(get_session)) -> RiskTierResponse:
    """Return all 7 risk axes for a server with trust-gating override applied.

    The risk_tier field reflects the published overall_risk after trust_gate caps
    CRITICAL/HIGH for verified publishers to MEDIUM.
    """
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

    axes: Dict[str, AxisData] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        axes[r.axis_name] = AxisData(label=r.label, p_top=r.p_top, probs=r.probs)
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)

    # published_overall_risk is the trust-gated tier (capped for verified publishers)
    published = gate.get("published_overall_risk") or labels.get("overall_risk")
    overall = labels.get("overall_risk")  # raw model label

    return RiskTierResponse(
        server_id=server_id,
        axes=axes,
        overall=overall,
        risk_tier=published,
        criteria_version=CRITERIA_VERSION,
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
    s.add(McpServerRegistry(server_id="srv_crit", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    # CRITICAL overall_risk for a verified publisher -> trust_gate caps to MEDIUM
    for _i, (ax, lbl) in enumerate((("overall_risk", "CRITICAL"),
                                    ("auth_strength", "STRONG"),
                                    ("capability_breadth", "BROAD"),
                                    ("data_sensitivity", "CRITICAL"),
                                    ("network_egress", "EXTERNAL"),
                                    ("maintainer_trust", "ESTABLISHED"),
                                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv_crit", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    # Non-verified server with CRITICAL (no override expected)
    s.add(McpServerRegistry(server_id="srv_unknown", name="Unknown MCP",
                            url="https://example.com/unknown-mcp"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "CRITICAL"),
                                    ("auth_strength", "WEAK"),
                                    ("capability_breadth", "BROAD"),
                                    ("data_sensitivity", "LOW"),
                                    ("network_egress", "NONE"),
                                    ("maintainer_trust", "UNKNOWN"),
                                    ("exploit_surface", "HIGH")), start=100):
        s.add(McpLlmAxisScore(id=_i, server_id="srv_unknown", axis_name=ax, label=lbl,
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
    c = TestClient(app)

    # Test verified publisher: CRITICAL -> MEDIUM (trust-gated)
    r = c.get("/api/risk-tier/srv_crit")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv_crit"
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    assert j["overall"] == "CRITICAL", j
    assert j["risk_tier"] == "MEDIUM", j  # Stripe = verified -> capped from CRITICAL to MEDIUM
    assert j["criteria_version"] == "v1.0"
    assert j["axes"]["overall_risk"]["label"] == "CRITICAL"
    assert j["axes"]["maintainer_trust"]["label"] == "ESTABLISHED"

    # Test non-verified publisher: CRITICAL stays CRITICAL (no override)
    r2 = c.get("/api/risk-tier/srv_unknown")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["risk_tier"] == "CRITICAL", j2  # no trust gate applied

    # Test 404 for unknown server
    assert c.get("/api/risk-tier/nosuch").status_code == 404

    print("PASS")
