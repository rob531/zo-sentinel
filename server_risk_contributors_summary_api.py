# deps: fastapi, pydantic, sqlalchemy
"""Server Risk Contributors Summary API.

Provides GET /servers/{server_id}/risk_contributors returning a detailed breakdown
of each risk axis contribution to the overall risk tier for a given MCP server.
Mirrors the structure of verdict_breakdown_api.py: reads McpLlmAxisScore rows via
SQLAlchemy (app.db / app.models), applies trust_gating_override.trust_gate() for the
published overall risk, and computes per-axis contribution percentages.
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

router = APIRouter(prefix="/api", tags=["risk_contributors"])

# Canonical axis order (overall_risk excluded from contributions)
AXES = (
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

# Label -> ordinal weight (higher = more risky).  Used to normalise contributions
# when an axis has no probability scores.
_LABEL_WEIGHT = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 3,
    "STRONG": 0,
    "WEAK": 2,
    "BROAD": 1,
    "NARROW": 0,
    "SENSITIVE": 2,
    "INSENSITIVE": 0,
    "EXTERNAL": 2,
    "INTERNAL": 0,
    "LOCAL_ONLY": 0,
    "VERIFIED": 0,
    "ESTABLISHED": 0,
    "UNKNOWN": 1,
    "MINIMAL": 0,
    "MODERATE": 1,
    "HIGH": 2,
    "EXTENSIVE": 2,
}


def _label_weight(label: Optional[str]) -> float:
    """Return a numeric weight for a label, defaulting to 0.5 for unknown labels."""
    if label is None:
        return 0.5
    return _LABEL_WEIGHT.get(label.upper(), 0.5)


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = (
        db.execute(
            select(McpLlmAxisScore.model_version)
            .where(McpLlmAxisScore.server_id == server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
        )
        .first()
    )
    return row[0] if row else None


class AxisContribution(BaseModel):
    axis_name: str
    label: Optional[str] = None
    contribution_score: float  # normalised percentage [0-100]
    raw_probability: Optional[float] = None  # p_top from DB if available


class RiskContributorsResponse(BaseModel):
    server_id: str
    overall_risk: str  # published (trust-gated) overall_risk label
    model_overall_risk: Optional[str] = None  # raw model label before gate
    axis_contributions: Dict[str, AxisContribution]


@router.get("/servers/{server_id}/risk_contributors", response_model=RiskContributorsResponse)
def get_risk_contributors(
    server_id: str, db: Session = Depends(get_session)
) -> RiskContributorsResponse:
    """Return per-axis contribution percentages and the published overall risk.

    Contribution score is derived from p_top (probability model assigns this label
    as the top prediction).  Axes with no p_top fall back to a label-weight heuristic.
    All contributions are normalised so they sum to 100.

    The published overall_risk is the trust-gating override result (official publishers
    such as Stripe/Microsoft/Google are capped at MEDIUM).
    """
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(
            status_code=404, detail=f"No scores for server_id {server_id!r}"
        )

    rows = (
        db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.model_version == mv,
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No scores for server_id {server_id!r}"
        )

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    # Build axis label map for trust gate
    labels: Dict[str, str] = {}
    for r in rows:
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)

    # Compute contributions
    raw_scores: Dict[str, float] = {}
    for r in rows:
        if r.axis_name == "overall_risk":
            continue
        if r.p_top is not None:
            raw_scores[r.axis_name] = r.p_top
        else:
            raw_scores[r.axis_name] = _label_weight(r.label)

    total = sum(raw_scores.values())
    if total == 0:
        total = 1.0  # avoid division by zero

    axis_contributions: Dict[str, AxisContribution] = {}
    for ax in AXES:
        if ax in raw_scores:
            score = (raw_scores[ax] / total) * 100.0
            row_for_ax = next((r for r in rows if r.axis_name == ax), None)
            axis_contributions[ax] = AxisContribution(
                axis_name=ax,
                label=labels.get(ax),
                contribution_score=round(score, 2),
                raw_probability=row_for_ax.p_top if row_for_ax else None,
            )
        else:
            axis_contributions[ax] = AxisContribution(
                axis_name=ax,
                label=labels.get(ax),
                contribution_score=0.0,
                raw_probability=None,
            )

    return RiskContributorsResponse(
        server_id=server_id,
        overall_risk=gate.get("published_overall_risk") or labels.get("overall_risk") or "UNKNOWN",
        model_overall_risk=gate.get("original_overall_risk") or labels.get("overall_risk"),
        axis_contributions=axis_contributions,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite for self-test
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed: one McpLlmAxisScore per axis using ONLY required constructor kwargs
    # (id, server_id, axis_name, label, model_version) -- no nullable/DB-default cols.
    s = SessionLocal()
    s.add(
        McpServerRegistry(
            server_id="srv_test1",
            name="Stripe MCP",
            url="https://github.com/stripe/agent-toolkit",
        )
    )
    axis_seed = [
        ("overall_risk", "HIGH"),
        ("auth_strength", "STRONG"),
        ("capability_breadth", "BROAD"),
        ("data_sensitivity", "CRITICAL"),
        ("network_egress", "EXTERNAL"),
        ("maintainer_trust", "ESTABLISHED"),
        ("exploit_surface", "MODERATE"),
    ]
    for i, (ax, lbl) in enumerate(axis_seed, start=1):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv_test1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
            )
        )
    # Second server with no rows
    s.add(
        McpServerRegistry(
            server_id="srv_test2",
            name="Unknown MCP",
            url="https://example.com/unknown",
        )
    )
    s.commit()
    s.close()

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    # --- Happy path ---
    resp = client.get("/api/servers/srv_test1/risk_contributors")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Required top-level keys
    assert "server_id" in data, data
    assert "overall_risk" in data, data
    assert "axis_contributions" in data, data

    contributions = data["axis_contributions"]
    assert isinstance(contributions, dict), data
    assert len(contributions) == 6, f"Expected 6 non-overall axes, got {len(contributions)}"

    # All 6 canonical axes present
    for ax in AXES:
        assert ax in contributions, f"Missing axis: {ax}"

    # Contributions sum to ~100%
    total_pct = sum(v["contribution_score"] for v in contributions.values())
    assert 99.0 <= total_pct <= 101.0, f"Contributions sum to {total_pct}, expected ~100"

    # overall_risk is MEDIUM after trust gate (Stripe is a verified publisher)
    assert data["overall_risk"] == "MEDIUM", f"Expected MEDIUM (trust-gated), got {data['overall_risk']}"

    # model_overall_risk is the raw HIGH before gate
    assert data.get("model_overall_risk") == "HIGH", data

    # --- Not found ---
    resp404 = client.get("/api/servers/srv_nomatch/risk_contributors")
    assert resp404.status_code == 404, f"Expected 404, got {resp404.status_code}"

    # --- Server with no axis rows ---
    resp_empty = client.get("/api/servers/srv_test2/risk_contributors")
    assert resp_empty.status_code == 404, f"Expected 404 for server with no rows, got {resp_empty.status_code}"

    print("PASS")
