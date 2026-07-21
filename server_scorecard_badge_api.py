"""server_scorecard_badge_api.py -- Compact scorecard + badge for a single MCP server.

Exposes GET /servers/{server_id}/scorecard  →  badge label, 7 axis p_top values,
overall composite percentile, risk_tier from mcp_server_registry, and criteria_version.

Reads the authoritative SFT axis scores from Postgres (McpLlmAxisScore) and the
registry metadata (McpServerRegistry) via the app DB session, then applies the
trust-gating override so official publishers are not shown as false HIGH/CRITICAL.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["scorecard"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


# Verdict badge labels (canonical set)
BADGE_LABELS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)

CRITERIA_VERSION = "v1.0_20250601"  # frozen criteria snapshot; bump on model/rule changes


class AxisEntry(BaseModel):
    axis_name: str
    p_top: Optional[float] = None


class ScorecardResponse(BaseModel):
    server_id: str
    badge_label: str                              # published verdict tier
    criteria_version: str                         # frozen rule/criteria version
    axes: Dict[str, Optional[float]]              # axis_name → p_top (None if unscored)
    composite_percentile: Optional[float] = None  # overall composite score percentile
    risk_tier: Optional[str] = None               # from mcp_server_registry
    trusted_override: bool = False               # trust_gate applied an override
    masquerade_flag: bool = False


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _composite_percentile(db: Session, server_id: str, model_version: str) -> Optional[float]:
    """Return the percentile rank of this server's overall p_top among all servers
    scored on the same model_version.  Returns a float in [0, 100] or None."""
    overall_rows = db.execute(
        select(McpLlmAxisScore.p_top).where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.model_version == model_version,
            McpLlmAxisScore.p_top.isnot(None),
        )
    ).scalars().all()
    if not overall_rows:
        return None
    target = db.execute(
        select(McpLlmAxisScore.p_top).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.model_version == model_version,
        )
    ).scalar()
    if target is None:
        return None
    n = len(overall_rows)
    below = sum(1 for v in overall_rows if v < target)
    return round(below / n * 100, 2)


def _badge_label(labels: Dict[str, str], trusted: bool, masquerade_flag: bool) -> str:
    """Map the axis-label set to one of the canonical badge labels."""
    if masquerade_flag:
        return "KNOWN_THREAT"
    if not trusted:
        overall = labels.get("overall_risk", "").upper()
        if overall in ("CRITICAL", "HIGH"):
            return "HIGH_RISK_ISOLATED"
        if overall in ("MEDIUM", "ELEVATED"):
            return "CAUTION_LIMITED"
        return "INSUFFICIENT"
    # trusted
    overall = labels.get("overall_risk", "").upper()
    if overall == "LOW":
        return "TRUSTED_GENERAL"
    if overall == "MEDIUM":
        return "TRUSTED_RESEARCH"
    return "ENTERPRISE_CONTROLLED"


@router.get("/servers/{server_id}/scorecard", response_model=ScorecardResponse)
def get_scorecard(server_id: str, db: Session = Depends(get_session)) -> ScorecardResponse:
    """Compact scorecard for a single server: badge, axis p_top, percentile, risk_tier."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    labels: Dict[str, str] = {}
    axes: Dict[str, Optional[float]] = {}
    for r in rows:
        labels[r.axis_name] = r.label or ""
        axes[r.axis_name] = r.p_top  # may be None

    reg = db.get(McpServerRegistry, server_id)
    risk_tier = reg.risk_tier if reg else None

    gate = trust_gate(reg.url if reg else None, reg.name if reg else None, labels)
    trusted = bool(gate.get("trusted"))
    masquerade_flag = bool(gate.get("masquerade_flag"))

    badge = _badge_label(labels, trusted, masquerade_flag)
    percentile = _composite_percentile(db, server_id, mv)

    return ScorecardResponse(
        server_id=server_id,
        badge_label=badge,
        criteria_version=CRITERIA_VERSION,
        axes=axes,
        composite_percentile=percentile,
        risk_tier=risk_tier,
        trusted_override=trusted or masquerade_flag,
        masquerade_flag=masquerade_flag,
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
    # Seed registry
    s.add(McpServerRegistry(server_id="test-server-1", name="Acme MCP",
                            url="https://github.com/acme/mcp", risk_tier="Tier-2"))
    # Seed 7 axis rows (only required constructor kwargs: id, server_id, axis_name, label, model_version)
    axis_data = [
        ("overall_risk", "LOW", 0.05),
        ("auth_strength", "STRONG", 0.10),
        ("capability_breadth", "MODERATE", 0.40),
        ("data_sensitivity", "LOW", 0.08),
        ("network_egress", "INTERNAL", 0.12),
        ("maintainer_trust", "ESTABLISHED", 0.15),
        ("exploit_surface", "LOW", 0.20),
    ]
    for i, (ax, lbl, ptop) in enumerate(axis_data, start=1):
        row = McpLlmAxisScore(id=i, server_id="test-server-1", axis_name=ax,
                              label=lbl, model_version="v3.0_40974559")
        row.p_top = ptop
        s.add(row)
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

    # Happy path
    r = c.get("/api/servers/test-server-1/scorecard")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert "badge_label" in j, f"Missing badge_label: {j}"
    assert j["badge_label"] in (
        "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT",
    ), f"Invalid badge_label: {j['badge_label']}"
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}: {list(j['axes'].keys())}"
    for ax in AXES:
        assert ax in j["axes"], f"Missing axis {ax} in {list(j['axes'].keys())}"
    assert "risk_tier" in j, f"Missing risk_tier: {j}"
    assert j["risk_tier"] == "Tier-2", f"Expected risk_tier='Tier-2', got {j['risk_tier']}"
    assert "criteria_version" in j, f"Missing criteria_version: {j}"
    assert "composite_percentile" in j, f"Missing composite_percentile: {j}"

    # Not-found path
    r2 = c.get("/api/servers/no-such-server/scorecard")
    assert r2.status_code == 404, f"Expected 404 for unknown server, got {r2.status_code}"

    print("PASS")
