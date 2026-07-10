"""server_verdict_api.py -- GET /servers/{server_id}/verdict

Reads the 7 risk axes from mcp_llm_axis_scores + risk_tier
from mcp_server_registry, applies trust_gating_override for the CRITICAL axis
override before returning the complete verdict payload.
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

router = APIRouter(prefix="/api", tags=["verdict"])


class AxisPayload(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Optional[dict] = None


class OverallPayload(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    label_index: Optional[int] = None


class VerdictPayload(BaseModel):
    axes: List[AxisPayload]
    overall: OverallPayload
    risk_tier: str
    trust_override_applied: bool
    criteria_version: str


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _risk_tier_from_axes(rows: list, reg_risk_tier: Optional[str]) -> str:
    """CRITICAL axis override: if any p_critical > 0.5, force to LOW regardless."""
    for r in rows:
        if r.p_critical is not None and r.p_critical > 0.5:
            return "LOW"
    return reg_risk_tier or "UNKNOWN"


@router.get("/servers/{server_id}/verdict", response_model=VerdictPayload)
def get_verdict(server_id: str, trust_override: bool = True,
                db: Session = Depends(get_session)) -> VerdictPayload:
    """Complete per-server verdict: 7 axis rows + overall + risk_tier with
    CRITICAL-axis override applied."""
    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found in registry")

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
        raise HTTPException(status_code=404, detail=f"No axis rows for server_id {server_id!r}")

    criteria_version = ""
    overall_row = None
    for r in rows:
        if r.decision_rule_version:
            criteria_version = r.decision_rule_version
        if r.axis_name == "overall_risk":
            overall_row = r
    if not criteria_version:
        criteria_version = "unknown"

    labels: Dict[str, str] = {}
    for r in rows:
        if r.label:
            labels[r.axis_name] = r.label

    overall_label = labels.get("overall_risk")
    overall_p_top = overall_p_critical = overall_p_danger = overall_label_index = None
    if overall_row:
        overall_p_top = overall_row.p_top
        overall_p_critical = overall_row.p_critical
        overall_p_danger = overall_row.p_danger
        overall_label_index = overall_row.label_index

    trust_override_applied = False
    final_overall_label = overall_label

    if trust_override:
        gate = trust_gate(reg.url, reg.name, labels)
        trust_override_applied = bool(gate.get("trusted") or gate.get("masquerade_flag"))
        if gate.get("trusted"):
            # Trusted publisher: cap published overall_risk, but keep the
            # registry risk_tier (the override prevents false HIGH/CRITICAL
            # being shown to users while preserving the internal tier).
            final_overall_label = gate.get("published_overall_risk") or overall_label

    # CRITICAL-axis override: p_critical > 0.5 forces risk_tier to LOW
    final_risk_tier = _risk_tier_from_axes(rows, reg.risk_tier)

    axes = [
        AxisPayload(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            probs=r.probs,
        )
        for r in sorted(rows, key=lambda x: x.label_index if x.label_index is not None else 999)
    ]

    return VerdictPayload(
        axes=axes,
        overall=OverallPayload(
            label=final_overall_label,
            p_top=overall_p_top,
            p_critical=overall_p_critical,
            p_danger=overall_p_danger,
            label_index=overall_label_index,
        ),
        risk_tier=final_risk_tier,
        trust_override_applied=trust_override_applied,
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
    s.add(McpServerRegistry(server_id="srv1", name="Test MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="HIGH_RISK_ISOLATED"))
    # Seed 7 axis rows with label_index for sort order -- REQUIRED kwargs only
    AXIS_DEFS = (
        ("overall_risk", "HIGH", 0),
        ("auth_strength", "STRONG", 1),
        ("capability_breadth", "BROAD", 2),
        ("data_sensitivity", "LOW", 3),
        ("network_egress", "EXTERNAL", 4),
        ("maintainer_trust", "ESTABLISHED", 5),
        ("exploit_surface", "MODERATE", 6),
    )
    for _i, (ax, lbl, idx) in enumerate(AXIS_DEFS, start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559", label_index=idx))
    # srv2: data_sensitivity has p_critical=0.6 -> forces risk_tier to LOW
    s.add(McpServerRegistry(server_id="srv2", name="Critical Test",
                            url="https://example.com/critical", risk_tier="HIGH_RISK_ISOLATED"))
    s.add(McpLlmAxisScore(id=20, server_id="srv2", axis_name="overall_risk",
                           label="LOW", model_version="v3.0_40974559", label_index=0))
    s.add(McpLlmAxisScore(id=21, server_id="srv2", axis_name="auth_strength",
                           label="WEAK", model_version="v3.0_40974559", label_index=1))
    s.add(McpLlmAxisScore(id=22, server_id="srv2", axis_name="data_sensitivity",
                           label="CRITICAL", model_version="v3.0_40974559",
                           label_index=3, p_critical=0.6))
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

    # Happy path: srv1 with trust override (Stripe = trusted publisher)
    r = c.get("/api/servers/srv1/verdict", params={"trust_override": True})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    assert j["risk_tier"] == "HIGH_RISK_ISOLATED", j
    assert j["criteria_version"] == "unknown"
    assert j["trust_override_applied"] is True, j
    assert j["overall"]["label"] == "MEDIUM", j  # Stripe capped from HIGH -> MEDIUM
    indices = [a["label_index"] for a in j["axes"] if a.get("label_index") is not None]
    assert indices == sorted(indices), f"Axes not sorted by label_index: {indices}"

    # CRITICAL axis override: p_critical > 0.5 forces risk_tier to LOW
    r2 = c.get("/api/servers/srv2/verdict", params={"trust_override": False})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["risk_tier"] == "LOW", f"CRITICAL axis should force LOW, got {j2['risk_tier']}"
    assert j2["trust_override_applied"] is False

    # Not found
    assert c.get("/api/servers/nope/verdict").status_code == 404

    print("PASS")
