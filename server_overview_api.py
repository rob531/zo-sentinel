"""server_overview_api.py -- Aggregated server overview endpoint.

Provides GET /servers/{server_id}/overview that combines axis scores,
overall risk (float), and registry metadata for a given MCP server.
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

router = APIRouter(prefix="/api", tags=["servers"])


class AxisDetail(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class ServerOverview(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    overall_risk: float
    axes: Dict[str, AxisDetail]
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    last_assessed: Optional[str] = None


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _axis_score_to_float(label: Optional[str]) -> float:
    """Convert a risk label to a numeric score 0-100."""
    mapping = {
        "CRITICAL": 100.0,
        "HIGH": 75.0,
        "MEDIUM": 50.0,
        "LOW": 25.0,
        "SAFE": 0.0,
    }
    return mapping.get((label or "").upper(), 50.0)


@router.get("/servers/{server_id}/overview", response_model=ServerOverview)
def get_server_overview(server_id: str, db: Session = Depends(get_session)) -> ServerOverview:
    """Aggregated overview for a single MCP server: axis scores, overall risk,
    and registry metadata including risk_tier, verdict, and last_assessed."""
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
    risk_tier = reg.risk_tier if reg else None
    verdict = reg.verdict if reg else None
    confidence = reg.confidence if reg else None
    last_assessed = reg.last_assessed.isoformat() if reg and reg.last_assessed else None

    axes: Dict[str, AxisDetail] = {}
    labels: Dict[str, str] = {}
    overall_label: Optional[str] = None
    overall_p_top: Optional[float] = None

    for r in rows:
        axes[r.axis_name] = AxisDetail(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
        )
        if r.label:
            labels[r.axis_name] = r.label
        if r.axis_name == "overall_risk":
            overall_label = r.label
            overall_p_top = r.p_top

    gate = trust_gate(url, name, labels)
    published_label = gate.get("published_overall_risk") or overall_label
    overall_risk = _axis_score_to_float(published_label)

    return ServerOverview(
        server_id=server_id,
        name=name,
        url=url,
        overall_risk=overall_risk,
        axes=axes,
        risk_tier=risk_tier,
        verdict=verdict,
        confidence=confidence,
        last_assessed=last_assessed,
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
                            url="https://github.com/test/mcp",
                            risk_tier="HIGH", verdict="reviewed",
                            confidence=0.85))
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

    # Happy path
    r = c.get("/api/servers/srv1/overview")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "server_id" in j, j
    assert "overall_risk" in j, j
    assert 0 <= j["overall_risk"] <= 100, j["overall_risk"]
    assert "axes" in j, j
    assert "risk_tier" in j, j
    assert "verdict" in j, j
    assert j["server_id"] == "srv1"
    assert j["risk_tier"] == "HIGH"
    assert j["verdict"] == "reviewed"
    assert len(j["axes"]) == 7, j

    # 404 for unknown server
    r2 = c.get("/api/servers/nope/overview")
    assert r2.status_code == 404, r2.text

    print("PASS")
