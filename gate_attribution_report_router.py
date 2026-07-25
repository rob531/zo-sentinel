"""gate_attribution_report_router.py -- per-gate attribution report endpoint.

Exposes GET /gate/{gate_id}/attribution_report returning risk-score attribution
for all servers in a gate, aggregated from mcp_llm_axis_scores + McpServerRegistry.

Data: app.db SQLAlchemy session (no DuckDB). Trust-gating applied to published tiers.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate

router = APIRouter(prefix="/gate", tags=["gate"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisAttribution(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    score: Optional[float] = None


class GateAttributionReport(BaseModel):
    gate_id: str
    axes: Dict[str, AxisAttribution]
    overall_risk: Optional[str] = None
    risk_tier: Optional[str] = None
    criteria_version: Optional[str] = None


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _compute_score(label: Optional[str]) -> Optional[float]:
    """Map a risk label to a numeric score for sorting/display."""
    mapping = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    return mapping.get(label or "", None)


@router.get("/{gate_id}/attribution_report", response_model=GateAttributionReport)
def get_gate_attribution_report(gate_id: str, db: Session = Depends(get_session)) -> GateAttributionReport:
    """Attribution report for a gate: aggregated axis scores + risk tier."""
    # Look up the gate (server) in the registry
    reg = db.get(McpServerRegistry, gate_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id!r} not found")

    mv = _latest_model_version(db, gate_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for gate {gate_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == gate_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    axes: Dict[str, AxisAttribution] = {}
    labels: Dict[str, str] = {}
    criteria_version: Optional[str] = None

    for r in rows:
        score = _compute_score(r.label)
        axes[r.axis_name] = AxisAttribution(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            score=score,
        )
        if r.label:
            labels[r.axis_name] = r.label
        if r.decision_rule_version:
            criteria_version = r.decision_rule_version

    gate = trust_gate(reg.url, reg.name, labels)
    overall_risk = gate.get("published_overall_risk") or labels.get("overall_risk")
    risk_tier = reg.risk_tier or overall_risk

    return GateAttributionReport(
        gate_id=gate_id,
        axes=axes,
        overall_risk=overall_risk,
        risk_tier=risk_tier,
        criteria_version=criteria_version,
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
    s.add(McpServerRegistry(server_id="gate_stripe", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="MEDIUM"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="gate_stripe", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559",
                              decision_rule_version="rules_v2.1"))
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

    # Happy path: known gate
    r = c.get("/gate/gate_stripe/attribution_report")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["gate_id"] == "gate_stripe", j
    assert "axes" in j, j
    assert "overall_risk" in j, j
    assert "risk_tier" in j, j
    # 7 axes expected
    assert len(j["axes"]) == 7, j
    # Stripe is trusted -> published MEDIUM
    assert j["overall_risk"] == "MEDIUM", j
    assert j["criteria_version"] == "rules_v2.1", j

    # Not found
    r2 = c.get("/gate/nonexistent/attribution_report")
    assert r2.status_code == 404, r2.text

    print("PASS")
