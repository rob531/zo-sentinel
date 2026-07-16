"""server_risk_contributors_api.py -- per-server risk contributor ranking.

Reads the 7 risk axes from mcp_llm_axis_scores for the latest model_version,
normalises each axis to a 0-100 contribution_score, and returns ranked
contributors with verdict/confidence from mcp_server_registry.

Mounted automatically by app.main via _OPTIONAL_ROUTERS.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk"])


# ===================== Pydantic models =====================

class ContributorAxis(BaseModel):
    axis: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    contribution_score: float


class RiskContributorsResponse(BaseModel):
    server_id: str
    overall_risk: str
    contributors: List[ContributorAxis]
    verdict: Optional[str] = None
    confidence: Optional[float] = None


# ===================== helpers =====================

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _compute_contribution_scores(rows: list, overall_risk_label: str) -> List[ContributorAxis]:
    """Normalise each axis's risk probability to 0-100 contribution_score.

    Strategy: weight = p_top * 2 + p_critical * 1.5 + p_danger
    Max weight across the 7 axes normalises the top contributor to 100.
    """
    weighted = []
    for r in rows:
        p_top = r.p_top or 0.0
        p_critical = r.p_critical or 0.0
        p_danger = r.p_danger or 0.0
        weight = p_top * 2.0 + p_critical * 1.5 + p_danger
        weighted.append(weight)

    max_w = max(weighted) if weighted else 1.0
    contributors = []
    for r, w in zip(rows, weighted):
        score = (w / max_w) * 100.0 if max_w > 0 else 0.0
        contributors.append(ContributorAxis(
            axis=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            contribution_score=round(score, 2),
        ))

    # Sort descending by contribution_score
    contributors.sort(key=lambda x: x.contribution_score, reverse=True)
    return contributors


# ===================== endpoint =====================

@router.get("/servers/{server_id}/risk-contributors", response_model=RiskContributorsResponse)
def get_server_risk_contributors(
        server_id: str,
        db: Session = Depends(get_session)) -> RiskContributorsResponse:
    """Return the 7 risk axes ranked by contribution_score (0-100), with
    overall_risk, verdict, and confidence from the registry."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404,
                            detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"No axis rows for server_id {server_id!r}")

    # Build a label dict for trust_gate
    labels: Dict[str, str] = {r.axis_name: r.label for r in rows if r.label}

    # overall_risk from the model
    overall_row = next((r for r in rows if r.axis_name == "overall_risk"), None)
    model_overall_risk = labels.get("overall_risk", "UNKNOWN")

    # Apply trust-gating to the published overall_risk
    reg = db.get(McpServerRegistry, server_id)
    url = reg.url if reg else None
    name = reg.name if reg else None
    gate = trust_gate(url, name, labels)
    published_overall_risk = (
        gate.get("published_overall_risk") or model_overall_risk
    )

    contributors = _compute_contribution_scores(rows, model_overall_risk)

    return RiskContributorsResponse(
        server_id=server_id,
        overall_risk=published_overall_risk,
        contributors=contributors,
        verdict=reg.verdict if reg else None,
        confidence=reg.confidence if reg else None,
    )


# ===================== self-test =====================

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

    # Seed: srv1 = HIGH overall with data_sensitivity as top contributor
    s = TS()
    s.add(McpServerRegistry(
        server_id="srv1", name="Stripe MCP",
        url="https://github.com/stripe/agent-toolkit",
        verdict="HIGH", confidence=0.88,
    ))
    # axis_data: (axis_name, label, p_top, p_critical, p_danger)
    # data_sensitivity CRITICAL has the highest weighted score (p_top*2 + p_critical*1.5 + p_danger)
    axis_data = [
        ("overall_risk",     "HIGH",       0.85, 0.80, 0.90),
        ("auth_strength",    "STRONG",     0.60, 0.55, 0.65),
        ("capability_breadth","BROAD",     0.70, 0.65, 0.75),
        ("data_sensitivity", "CRITICAL",  0.95, 0.92, 0.98),  # highest weighted
        ("network_egress",   "EXTERNAL",   0.50, 0.45, 0.55),
        ("maintainer_trust", "ESTABLISHED",0.65, 0.60, 0.70),
        ("exploit_surface",  "MODERATE",   0.40, 0.35, 0.45),
    ]
    for i, (ax, lbl, p_top, p_crit, p_danger) in enumerate(axis_data, start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax,
                              label=lbl, model_version="v3.0_40974559",
                              p_top=p_top, p_critical=p_crit, p_danger=p_danger))
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

    # Happy path
    r = c.get("/api/servers/srv1/risk-contributors")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert j["server_id"] == "srv1", j
    assert j["overall_risk"] == "MEDIUM", j   # Stripe = verified publisher, capped
    assert j["verdict"] == "HIGH", j
    assert j["confidence"] == 0.88, j
    assert len(j["contributors"]) == 7, f"Expected 7 contributors, got {len(j['contributors'])}"
    assert all(isinstance(c["contribution_score"], (int, float)) for c in j["contributors"]), j
    scores = [c["contribution_score"] for c in j["contributors"]]
    assert all(s > 0 for s in scores), f"All scores must be > 0: {scores}"
    # Top contributor is the one with the highest score
    top = max(j["contributors"], key=lambda x: x["contribution_score"])
    assert top["axis"] == "data_sensitivity", f"Expected data_sensitivity top, got {top['axis']}"
    # Scores must be descending
    assert scores == sorted(scores, reverse=True), f"Scores not sorted: {scores}"

    # 404 for unknown server
    r2 = c.get("/api/servers/nope/risk-contributors")
    assert r2.status_code == 404, r2.text

    print("PASS")
