"""server_risk_overview_api.py -- Per-server risk overview endpoint.

Returns the server's current risk_tier, latest overall_risk axis label, and a
list of the most-recent axis scores (all 7 axes). Trust-gating is applied so
official publishers are not shown as false HIGH/CRITICAL.

Mounted by app.main via _OPTIONAL_ROUTERS (exposes `router`).
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

router = APIRouter(prefix="/api", tags=["risk"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisScoreItem(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    scored_at: Optional[str] = None


class ServerRiskOverview(BaseModel):
    server_id: str
    risk_tier: Optional[str] = None
    overall_risk: Optional[str] = None
    axis_scores: list[AxisScoreItem]


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def get_server_risk_overview(server_id: str, db: Session) -> dict:
    """Return risk overview dict for the given server_id.

    Reads the latest model_version's axis rows from mcp_llm_axis_scores and
    the risk_tier from mcp_server_registry, then applies trust_gating_override.
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
    risk_tier = reg.risk_tier if reg else None

    axis_scores: list[AxisScoreItem] = []
    labels: Dict[str, str] = {}
    for r in rows:
        scored_at_str = None
        if r.scored_at:
            scored_at_str = r.scored_at.isoformat() if hasattr(r.scored_at, "isoformat") else str(r.scored_at)
        axis_scores.append(AxisScoreItem(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            scored_at=scored_at_str,
        ))
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(reg.url if reg else None, reg.name if reg else None, labels)
    published_overall_risk = gate.get("published_overall_risk") or labels.get("overall_risk")

    return {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "overall_risk": published_overall_risk,
        "axis_scores": axis_scores,
    }


@router.get("/servers/{server_id}/risk-overview", response_model=ServerRiskOverview)
def server_risk_overview(server_id: str, db: Session = Depends(get_session)) -> ServerRiskOverview:
    """Per-server risk overview: risk_tier, published overall_risk, and all 7 axis scores."""
    result = get_server_risk_overview(server_id, db)
    return ServerRiskOverview(**result)


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
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="LOW"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
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

    r = c.get("/api/servers/srv1/risk-overview")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "risk_tier" in j, j
    assert "overall_risk" in j, j
    assert "axis_scores" in j, j
    assert j["risk_tier"] == "LOW", j
    assert j["overall_risk"] == "MEDIUM", j  # Stripe = verified -> capped from HIGH to MEDIUM
    assert len(j["axis_scores"]) == 7, j
    axis_names = {a["axis_name"] for a in j["axis_scores"]}
    assert axis_names == set(AXES), axis_names

    # 404 for unknown server
    assert c.get("/api/servers/nope/risk-overview").status_code == 404

    print("PASS")
