# deps: requests
"""FastAPI router exposing GET /servers/{server_id}/risk_tier.

Reads 7 axis scores from mcp_llm_axis_scores via the app data layer, computes
the overall risk tier, and applies trust_gating_override overrides.

Mirrors verdict_breakdown_api.py patterns -- real app.db/app.models imports,
no inline DB stubs.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/servers", tags=["risk"])


class AxisScoreData(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class RiskTierResponse(BaseModel):
    overall_risk: float
    risk_tier: str
    axes: Dict[str, AxisScoreData]
    override_applied: bool


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _risk_tier_from_p_top(p_top: float) -> str:
    """Map combined p_top probability to a risk tier label."""
    if p_top >= 0.9:
        return "CRITICAL"
    elif p_top >= 0.7:
        return "HIGH"
    elif p_top >= 0.4:
        return "MEDIUM"
    elif p_top >= 0.15:
        return "LOW"
    else:
        return "CLEAR"


def get_risk_tier(server_id: str, db: Session) -> RiskTierResponse:
    """Compute the overall risk tier for a server from its 7 axis scores.

    Reads the latest model_version rows for server_id, combines axis p_top
    values into an overall risk probability, applies trust_gating_override,
    and returns the structured response.

    Returns:
        dict with keys: overall_risk (float), risk_tier (str),
                        axes (dict), override_applied (bool)
    """
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server_id {server_id!r}"
        )

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server_id {server_id!r}"
        )

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    # Build axis dict and labels for trust gate
    axes: Dict[str, AxisScoreData] = {}
    labels: Dict[str, str] = {}
    p_tops = []
    for r in rows:
        axes[r.axis_name] = AxisScoreData(
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
        )
        if r.label:
            labels[r.axis_name] = r.label
        if r.p_top is not None:
            p_tops.append(r.p_top)

    # Combine p_top across all axes (average)
    overall_risk = sum(p_tops) / len(p_tops) if p_tops else 0.0

    # Apply trust gate
    gate = trust_gate(url, name, labels)
    model_overall_risk = gate.get("original_overall_risk") or labels.get("overall_risk")
    published_overall_risk = gate.get("published_overall_risk") or labels.get("overall_risk")

    # Compute risk tier from combined p_top
    risk_tier = _risk_tier_from_p_top(overall_risk)

    # Check if trust gate changed the published overall risk
    override_applied = (
        model_overall_risk is not None
        and published_overall_risk is not None
        and model_overall_risk != published_overall_risk
    )

    return RiskTierResponse(
        overall_risk=round(overall_risk, 4),
        risk_tier=risk_tier,
        axes=axes,
        override_applied=override_applied,
    )


@router.get("/{server_id}/risk_tier", response_model=RiskTierResponse)
def read_risk_tier(
    server_id: str,
    db: Session = Depends(get_session),
) -> RiskTierResponse:
    """GET /servers/{server_id}/risk_tier.

    Returns the combined risk probability, computed risk tier, per-axis
    score details, and whether a trust-gating override was applied.
    """
    return get_risk_tier(server_id, db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: Stripe server with HIGH overall_risk (trust gate should cap it)
    # + unknown server with no override
    s = TS()
    s.add(McpServerRegistry(
        server_id="srv-trusted", name="Stripe MCP",
        url="https://github.com/stripe/agent-toolkit"))
    s.add(McpServerRegistry(
        server_id="srv-unknown", name="Unknown MCP",
        url="https://example.com/unknown"))
    # Required kwargs for McpLlmAxisScore: id, server_id, axis_name, label, model_version
    # No other columns -- they have DB defaults.
    axes_trusted = [
        ("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
        ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
        ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
        ("exploit_surface", "MODERATE"),
    ]
    for i, (ax, lbl) in enumerate(axes_trusted, start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv-trusted",
                               axis_name=ax, label=lbl,
                               model_version="v3.0_40974559",
                               p_top=0.85 if ax == "overall_risk" else 0.4,
                               p_critical=0.3 if ax == "overall_risk" else 0.1,
                               p_danger=0.5 if ax == "overall_risk" else 0.2))

    axes_unknown = [
        ("overall_risk", "MEDIUM"), ("auth_strength", "MODERATE"),
        ("capability_breadth", "LIMITED"), ("data_sensitivity", "LOW"),
        ("network_egress", "INTERNAL"), ("maintainer_trust", "UNKNOWN"),
        ("exploit_surface", "MINIMAL"),
    ]
    for i, (ax, lbl) in enumerate(axes_unknown, start=8):
        s.add(McpLlmAxisScore(id=i, server_id="srv-unknown",
                               axis_name=ax, label=lbl,
                               model_version="v3.0_40974559",
                               p_top=0.35 if ax == "overall_risk" else 0.2,
                               p_critical=0.1, p_danger=0.15))
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

    # Happy path: trusted server (HIGH -> MEDIUM via trust gate)
    r = c.get("/servers/srv-trusted/risk_tier")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["override_applied"] is True, j
    assert j["overall_risk"] is not None, j
    assert len(j["axes"]) == 7, j
    assert j["axes"]["overall_risk"]["label"] == "HIGH", j

    # Happy path: unknown server (no override)
    r2 = c.get("/servers/srv-unknown/risk_tier")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["override_applied"] is False, j2
    assert j2["axes"]["overall_risk"]["label"] == "MEDIUM", j2

    # Edge case: server not found
    r3 = c.get("/servers/nope/risk_tier")
    assert r3.status_code == 404, r3.text

    print("PASS")
