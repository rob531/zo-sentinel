"""verdict_criteria_api.py -- Exposes the current risk-tier thresholds and axis
weights used by trust_gating_override to map mcp_llm_axis_scores axis data to
a risk_tier. Read-only; no DB writes.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore
from trust_gating_override import RISK_ORDER, CAP_TIER

router = APIRouter(prefix="/api", tags=["verdict"])


class TierDefinition(BaseModel):
    tier: str
    min_composite: int
    description: str


class AxisDefinition(BaseModel):
    axis_name: str
    label: Optional[str] = None
    weight: Optional[float] = None
    notes: Optional[str] = None


class VerdictCriteria(BaseModel):
    criteria_version: str
    tiers: list[TierDefinition]
    axes: list[AxisDefinition]


# Axis metadata: weight (relative importance) and descriptive notes
# These are derived from the trust_gating_override contract and the SFT model.
AXIS_METADATA: dict[str, dict] = {
    "overall_risk": {
        "weight": 1.0,
        "notes": "Primary composite risk label derived from all axis scores"
    },
    "auth_strength": {
        "weight": 0.85,
        "notes": "Strength of authentication mechanisms (STRONG/WEAK)"
    },
    "capability_breadth": {
        "weight": 0.6,
        "notes": "Scope of capabilities exposed (BROAD/LIMITED)"
    },
    "data_sensitivity": {
        "weight": 0.9,
        "notes": "Sensitivity of data accessed (CRITICAL/HIGH/MEDIUM/LOW)"
    },
    "network_egress": {
        "weight": 0.7,
        "notes": "External network reachability (EXTERNAL/RESTRICTED)"
    },
    "maintainer_trust": {
        "weight": 0.8,
        "notes": "Maintainer credibility (VERIFIED/ESTABLISHED/UNKNOWN/WEAK)"
    },
    "exploit_surface": {
        "weight": 0.75,
        "notes": "Potential attack surface (HIGH/MODERATE/LOW)"
    },
}

# Static tier definitions matching the RISK_ORDER from trust_gating_override
TIER_DEFINITIONS: list[dict] = [
    {
        "tier": "LOW",
        "min_composite": RISK_ORDER["LOW"],
        "description": "Low risk: minimal sensitive data access, strong auth, restricted egress"
    },
    {
        "tier": "MEDIUM",
        "min_composite": RISK_ORDER["MEDIUM"],
        "description": "Medium risk: moderate capability surface, some sensitive data access"
    },
    {
        "tier": "HIGH",
        "min_composite": RISK_ORDER["HIGH"],
        "description": "High risk: broad capabilities, significant data access or external egress"
    },
    {
        "tier": "CRITICAL",
        "min_composite": RISK_ORDER["CRITICAL"],
        "description": "Critical risk: extensive sensitive data access, weak auth, broad surface"
    },
]


def _get_criteria_version(db: Session) -> str:
    """Get the most recent decision_rule_version from any scored axis row."""
    row = db.execute(
        select(McpLlmAxisScore.decision_rule_version)
        .where(McpLlmAxisScore.decision_rule_version.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    if row and row[0]:
        return str(row[0])
    # Fallback: derive from the most recent model_version
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return str(row[0]) if row else "unknown"


def _get_unique_rule_versions(db: Session) -> set:
    """Return the set of distinct decision_rule_versions seen across all scores."""
    rows = db.execute(
        select(McpLlmAxisScore.decision_rule_version)
        .where(McpLlmAxisScore.decision_rule_version.isnot(None))
        .distinct()
    ).scalars().all()
    return {r for r in rows if r}


@router.get("/verdict/criteria", response_model=VerdictCriteria)
def get_verdict_criteria(db: Session = Depends(get_session)) -> VerdictCriteria:
    """Return the current risk-tier thresholds and axis weights.

    Reads decision_rule_version from the most recent scored row in
    mcp_llm_axis_scores. Returns tier definitions and axis metadata
    used by trust_gating_override to map scores to risk_tier.

    Response shape:
      - criteria_version: the decision_rule_version in use
      - tiers: ordered list of tier definitions with min_composite threshold
      - axes: list of all 7 risk axes with weights and descriptive notes
    """
    criteria_version = _get_criteria_version(db)

    tiers = [TierDefinition(**t) for t in TIER_DEFINITIONS]

    axes = []
    for axis_name, meta in AXIS_METADATA.items():
        axes.append(AxisDefinition(
            axis_name=axis_name,
            weight=meta.get("weight"),
            notes=meta.get("notes"),
        ))

    return VerdictCriteria(
        criteria_version=criteria_version,
        tiers=tiers,
        axes=axes,
    )


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
    # Seed a server with axis scores including decision_rule_version
    for i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv_test", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559",
                              decision_rule_version="rule_v2_20250601"))
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

    # Happy path: criteria returns non-empty tiers + axes
    r = c.get("/api/verdict/criteria")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert "criteria_version" in j, f"Missing criteria_version: {j}"
    assert len(j["tiers"]) >= 4, f"Expected at least 4 tiers, got {len(j['tiers'])}: {j}"
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}: {j}"

    # Verify tier structure
    tier_names = [t["tier"] for t in j["tiers"]]
    assert "LOW" in tier_names, f"Missing LOW tier: {j}"
    assert "MEDIUM" in tier_names, f"Missing MEDIUM tier: {j}"
    assert "HIGH" in tier_names, f"Missing HIGH tier: {j}"
    assert "CRITICAL" in tier_names, f"Missing CRITICAL tier: {j}"

    # Verify axis structure
    axis_names = [a["axis_name"] for a in j["axes"]]
    expected_axes = {"overall_risk", "auth_strength", "capability_breadth",
                    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"}
    assert set(axis_names) == expected_axes, f"Unexpected axes: {axis_names}"

    # Verify weights are present
    for axis in j["axes"]:
        assert axis.get("weight") is not None, f"Missing weight for {axis['axis_name']}"
        assert axis.get("notes") is not None, f"Missing notes for {axis['axis_name']}"

    print("PASS")
