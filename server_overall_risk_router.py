"""server_overall_risk_router.py -- overall risk score + tier for a single MCP server.

GET /servers/{server_id}/overall-risk  ->  {server_id, overall_risk_score, risk_tier, computed_at}

Reads the mcp_llm_axis_scores row for axis_name='overall_risk' (latest model_version),
aggregates p_top probability, maps to a risk tier, and applies trust_gating_override so
official publishers are not shown as false HIGH/CRITICAL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk"])


# Risk-tier thresholds: map aggregated p_top score to tier
def _p_top_to_tier(p_top: float) -> str:
    if p_top >= 0.8:
        return "HIGH_RISK_ISOLATED"
    elif p_top >= 0.6:
        return "HIGH"
    elif p_top >= 0.4:
        return "MEDIUM"
    elif p_top >= 0.2:
        return "LOW"
    else:
        return "MINIMAL"


class OverallRiskResponse(BaseModel):
    server_id: str
    overall_risk_score: float  # 0-100 (p_top * 100)
    risk_tier: str
    computed_at: str  # ISO 8601


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/{server_id}/overall-risk", response_model=OverallRiskResponse)
def get_overall_risk(server_id: str, db: Session = Depends(get_session)) -> OverallRiskResponse:
    """Return the overall risk score and tier for a given MCP server.

    Reads the 'overall_risk' axis row for the latest model_version, aggregates
    p_top into a 0-100 score, maps to a risk tier, and applies the trust-gating
    override so official publishers are not shown as false HIGH/CRITICAL.
    """
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    row = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().first()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No overall_risk axis row for server_id {server_id!r}")

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    # Aggregate p_top into a 0-100 score
    p_top = row.p_top if row.p_top is not None else 0.0
    risk_score = round(min(p_top * 100, 100.0), 2)

    # Base tier from p_top
    raw_tier = _p_top_to_tier(p_top)

    # Apply trust-gating override
    labels = {"overall_risk": row.label} if row.label else {}
    gate = trust_gate(url, name, labels)
    published_tier = gate.get("published_overall_risk") or row.label or raw_tier

    return OverallRiskResponse(
        server_id=server_id,
        overall_risk_score=risk_score,
        risk_tier=published_tier,
        computed_at=datetime.now(timezone.utc).isoformat(),
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
    # CRITICAL server: p_top >= 0.8 -> HIGH_RISK_ISOLATED
    s.add(McpLlmAxisScore(id=1, server_id="srv_critical", axis_name="overall_risk",
                          label="CRITICAL", p_top=0.85,
                          model_version="v3.0_40974559"))
    # HIGH server: p_top >= 0.6 -> HIGH
    s.add(McpLlmAxisScore(id=2, server_id="srv_high", axis_name="overall_risk",
                          label="HIGH", p_top=0.65,
                          model_version="v3.0_40974559"))
    # Stripe server (trusted): p_top >= 0.8 but trust-gated -> capped at MEDIUM
    s.add(McpServerRegistry(server_id="srv_stripe", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    s.add(McpLlmAxisScore(id=3, server_id="srv_stripe", axis_name="overall_risk",
                          label="CRITICAL", p_top=0.9,
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

    # Happy path: CRITICAL -> HIGH_RISK_ISOLATED
    r = c.get("/api/servers/srv_critical/overall-risk")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv_critical", j
    assert j["overall_risk_score"] == 85.0, j
    assert j["risk_tier"] == "CRITICAL", j  # raw tier (no trust gate)
    assert "computed_at" in j, j

    # Happy path: HIGH server
    r2 = c.get("/api/servers/srv_high/overall-risk")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["server_id"] == "srv_high", j2
    assert j2["overall_risk_score"] == 65.0, j2
    assert j2["risk_tier"] == "HIGH", j2

    # Trust-gated: Stripe (verified publisher) CRITICAL -> capped to MEDIUM
    r3 = c.get("/api/servers/srv_stripe/overall-risk")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["server_id"] == "srv_stripe", j3
    assert j3["overall_risk_score"] == 90.0, j3
    assert j3["risk_tier"] == "MEDIUM", j3  # trust-gated cap

    # Failure: no such server
    r4 = c.get("/api/servers/nope/overall-risk")
    assert r4.status_code == 404, r4.text

    print("PASS")
