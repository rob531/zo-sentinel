"""server_risk_tier_readback_api_v2.py -- readback of stored risk-tier + 7 LLM axes.

GET /servers/{server_id}/risk-tier-readback returns:
  - stored risk_tier + last_scanned from mcp_server_registry
  - 7 axis rows (label, p_top, p_critical, p_danger, label_index) from mcp_llm_axis_scores
  - trust-gating override (published_overall_risk, trusted) via trust_gate()
  - verdict_reasoning + criteria_version (decision_rule_version)

Mounted by app.main via _OPTIONAL_ROUTERS. Mirrors verdict_breakdown_api.py:
real app.db / app.models imports, SQLAlchemy queries, trust_gate applied.
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

router = APIRouter(prefix="/servers", tags=["servers"])


# ---- Pydantic models ----

class AxisDetail(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    label_index: Optional[int] = None


class RiskTierReadbackResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_risk_tier: Optional[str] = None
    axes: Dict[str, AxisDetail]
    overall_risk: Optional[str] = None
    published_overall_risk: Optional[str] = None
    trusted: bool = False
    last_scanned: Optional[str] = None
    verdict_reasoning: Optional[str] = None
    criteria_version: Optional[str] = None


# ---- Helpers ----

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _latest_decision_rule_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.decision_rule_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


# ---- Endpoint ----

@router.get("/{server_id}/risk-tier-readback", response_model=RiskTierReadbackResponse)
def get_risk_tier_readback(
    server_id: str,
    db: Session = Depends(get_session),
) -> RiskTierReadbackResponse:
    """Return stored risk_tier + 7 LLM axes with trust-gating applied."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore)
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No axis scores for server_id {server_id!r}")

    reg = db.get(McpServerRegistry, server_id)

    axes_out: Dict[str, AxisDetail] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        axes_out[r.axis_name] = AxisDetail(
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            label_index=r.label_index,
        )
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(reg.url if reg else None, reg.name if reg else None, labels)

    last_scanned_iso = None
    if reg and reg.last_scanned:
        last_scanned_iso = reg.last_scanned.isoformat()

    criteria_version = _latest_decision_rule_version(db, server_id)

    return RiskTierReadbackResponse(
        server_id=server_id,
        name=reg.name if reg else None,
        registry_risk_tier=reg.risk_tier if reg else None,
        axes=axes_out,
        overall_risk=labels.get("overall_risk"),
        published_overall_risk=gate.get("published_overall_risk"),
        trusted=bool(gate.get("trusted")),
        last_scanned=last_scanned_iso,
        verdict_reasoning=reg.verdict_reasoning if reg else None,
        criteria_version=criteria_version,
    )


# ---- Self-test (in-memory SQLite via dependency override) ----

if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    sess = TS()
    now = datetime.now(timezone.utc)

    # registry entry with risk_tier + last_scanned + verdict_reasoning
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="Stripe MCP",
            url="https://github.com/stripe/agent-toolkit",
            risk_tier="MEDIUM",
            last_scanned=now,
            verdict_reasoning="Verified official publisher with established trust.",
        )
    )

    # 7 axis rows for srv1
    for _i, (ax, lbl, idx) in enumerate(
        [
            ("overall_risk", "HIGH", 3),
            ("auth_strength", "STRONG", 0),
            ("capability_breadth", "BROAD", 2),
            ("data_sensitivity", "CRITICAL", 4),
            ("network_egress", "EXTERNAL", 3),
            ("maintainer_trust", "ESTABLISHED", 1),
            ("exploit_surface", "MODERATE", 2),
        ],
        start=1,
    ):
        sess.add(
            McpLlmAxisScore(
                id=_i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
                label_index=idx,
                p_top=0.20,
                p_critical=0.30,
                p_danger=0.45,
                decision_rule_version="rule_v2_2024",
                scored_at=now,
            )
        )

    # second server: untrusted
    sess.add(
        McpServerRegistry(
            server_id="srv2",
            name="Unknown MCP",
            url="https://github.com/random-user/unknown-mcp",
            risk_tier="CRITICAL",
            last_scanned=now,
            verdict_reasoning="Unverified publisher.",
        )
    )
    for _i, (ax, lbl, idx) in enumerate(
        [
            ("overall_risk", "CRITICAL", 4),
            ("auth_strength", "WEAK", 3),
            ("capability_breadth", "BROAD", 2),
            ("data_sensitivity", "CRITICAL", 4),
            ("network_egress", "EXTERNAL", 3),
            ("maintainer_trust", "UNKNOWN", 2),
            ("exploit_surface", "LARGE", 3),
        ],
        start=201,
    ):
        sess.add(
            McpLlmAxisScore(
                id=_i,
                server_id="srv2",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
                label_index=idx,
                p_top=0.10,
                p_critical=0.60,
                p_danger=0.25,
                decision_rule_version="rule_v2_2024",
                scored_at=now,
            )
        )

    sess.commit()
    sess.close()

    # Test 1: trusted publisher (Stripe) -> published_overall_risk capped to MEDIUM
    resp1 = client.get("/servers/srv1/risk-tier-readback")
    assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}: {resp1.text}"
    data1 = resp1.json()
    assert data1["server_id"] == "srv1"
    assert data1["name"] == "Stripe MCP"
    assert data1["registry_risk_tier"] == "MEDIUM"
    assert len(data1["axes"]) == 7, f"Expected 7 axes, got {len(data1['axes'])}"
    assert data1["overall_risk"] == "HIGH"
    assert data1["published_overall_risk"] == "MEDIUM", f"Stripe should be capped, got {data1['published_overall_risk']}"
    assert data1["trusted"] is True, f"Stripe should be trusted, got trusted={data1['trusted']}"
    assert data1["last_scanned"] is not None
    assert data1["verdict_reasoning"] == "Verified official publisher with established trust."
    assert data1["criteria_version"] == "rule_v2_2024"
    for ax in AXES:
        assert ax in data1["axes"], f"Missing axis {ax}"
        a = data1["axes"][ax]
        assert "label" in a
        assert "p_top" in a
        assert "p_critical" in a
        assert "p_danger" in a
        assert "label_index" in a

    # Test 2: unknown publisher -> not trusted, published_overall_risk unchanged
    resp2 = client.get("/servers/srv2/risk-tier-readback")
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
    data2 = resp2.json()
    assert data2["server_id"] == "srv2"
    assert data2["registry_risk_tier"] == "CRITICAL"
    assert data2["overall_risk"] == "CRITICAL"
    assert data2["published_overall_risk"] == "CRITICAL", f"Unknown should not be capped, got {data2['published_overall_risk']}"
    assert data2["trusted"] is False, f"Unknown should not be trusted, got trusted={data2['trusted']}"
    assert len(data2["axes"]) == 7

    # Test 3: unknown server returns 404
    resp3 = client.get("/servers/nope/risk-tier-readback")
    assert resp3.status_code == 404, f"Expected 404, got {resp3.status_code}"

    # Test 4: all 7 axes present with correct field shapes
    for ax in AXES:
        assert ax in data1["axes"]
        a = data1["axes"][ax]
        assert isinstance(a["label"], str) or a["label"] is None
        assert isinstance(a["label_index"], int) or a["label_index"] is None

    print("PASS")