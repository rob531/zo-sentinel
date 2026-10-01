# deps: fastapi, pydantic, sqlalchemy
"""Router for verdict_distribution_summary -- summary of verdict/axis distributions across MCP servers."""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repo root is on path so `from app.db` resolves correctly
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict_distribution_summary"])


# --- Pydantic models -------------------------------------------------------

class AxisDistributionItem(BaseModel):
    axis_name: str
    label: str
    count: int
    pct: float


class AxisDistributionResponse(BaseModel):
    axis_name: str
    total_servers: int
    distribution: list[AxisDistributionItem]


class VerdictDistributionItem(BaseModel):
    verdict: str
    count: int
    pct: float


class VerdictDistributionResponse(BaseModel):
    total_servers: int
    distribution: list[VerdictDistributionItem]


class RiskTierItem(BaseModel):
    tier: str
    count: int
    pct: float


class RiskTierDistributionResponse(BaseModel):
    total_servers: int
    tiers: list[RiskTierItem]


class ServerCountResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int


class SummaryResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    verdict_distribution: list[VerdictDistributionItem]
    risk_tier_distribution: list[RiskTierItem]
    axis_names: list[str]
    generated_at: str


# --- Helpers ---------------------------------------------------------------

def _latest_model_version(db: Session) -> Optional[str]:
    row = (
        db.query(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
        .scalar()
    )
    return row


# --- Endpoints ------------------------------------------------------------

@router.get("/verdict_distribution_summary/distribution", response_model=VerdictDistributionResponse)
def get_verdict_distribution(
    db: Session = Depends(get_session),
) -> VerdictDistributionResponse:
    """Return distribution of verdicts across all servers in mcp_server_registry."""
    total = db.query(McpServerRegistry).count()

    rows = (
        db.query(
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.verdict)
        .all()
    )

    distribution = [
        VerdictDistributionItem(
            verdict=r.verdict or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in rows
    ]

    return VerdictDistributionResponse(
        total_servers=total,
        distribution=sorted(distribution, key=lambda x: -x.count),
    )


@router.get("/verdict_distribution_summary/risk-tier", response_model=RiskTierDistributionResponse)
def get_risk_tier_distribution(
    db: Session = Depends(get_session),
) -> RiskTierDistributionResponse:
    """Return distribution of risk_tier values across servers."""
    total = db.query(McpServerRegistry).count()

    rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    tiers = [
        RiskTierItem(
            tier=r.risk_tier or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in rows
    ]

    return RiskTierDistributionResponse(
        total_servers=total,
        tiers=sorted(tiers, key=lambda x: -x.count),
    )


@router.get("/verdict_distribution_summary/server-counts", response_model=ServerCountResponse)
def get_server_counts(
    db: Session = Depends(get_session),
) -> ServerCountResponse:
    """Return counts of total, scored, and unscored servers."""
    total = db.query(McpServerRegistry).count()

    model_ver = _latest_model_version(db)
    scored_q = db.query(McpLlmAxisScore.server_id).distinct()
    if model_ver:
        scored_q = scored_q.filter(McpLlmAxisScore.model_version == model_ver)
    scored_ids = {row[0] for row in scored_q.all()}
    scored_count = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id.in_(scored_ids)
    ).count()
    unscored_count = total - scored_count

    return ServerCountResponse(
        total_servers=total,
        scored_servers=scored_count,
        unscored_servers=unscored_count,
    )


@router.get("/verdict_distribution_summary/axis/{axis_name}", response_model=AxisDistributionResponse)
def get_axis_distribution(
    axis_name: str,
    db: Session = Depends(get_session),
) -> AxisDistributionResponse:
    """Return distribution of labels for a specific scoring axis
    (e.g. overall_risk, auth_strength, capability_breadth,
    data_sensitivity, network_egress, maintainer_trust, exploit_surface).
    """
    model_ver = _latest_model_version(db)

    base_q = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.axis_name == axis_name)
    if model_ver:
        base_q = base_q.filter(McpLlmAxisScore.model_version == model_ver)

    total = base_q.count()

    rows = (
        base_q
        .with_entities(
            McpLlmAxisScore.label,
            func.count(McpLlmAxisScore.id).label("cnt"),
        )
        .group_by(McpLlmAxisScore.label)
        .all()
    )

    distribution = [
        AxisDistributionItem(
            axis_name=axis_name,
            label=r.label or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in rows
    ]

    return AxisDistributionResponse(
        axis_name=axis_name,
        total_servers=total,
        distribution=sorted(distribution, key=lambda x: -x.count),
    )


@router.get("/verdict_distribution_summary/summary", response_model=SummaryResponse)
def get_summary(
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """Return a combined summary: server counts + verdict distribution + risk tier distribution + axis names."""
    total = db.query(McpServerRegistry).count()

    # Scored servers
    model_ver = _latest_model_version(db)
    scored_q = db.query(McpLlmAxisScore.server_id).distinct()
    if model_ver:
        scored_q = scored_q.filter(McpLlmAxisScore.model_version == model_ver)
    scored_ids = {row[0] for row in scored_q.all()}
    scored_count = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id.in_(scored_ids)
    ).count()
    unscored_count = total - scored_count

    # Verdict distribution
    verdict_rows = (
        db.query(
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.verdict)
        .all()
    )
    verdict_distribution = [
        VerdictDistributionItem(
            verdict=r.verdict or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in verdict_rows
    ]

    # Risk tier distribution
    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    risk_tier_distribution = [
        RiskTierItem(
            tier=r.risk_tier or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in tier_rows
    ]

    # All axis names
    axis_names = [
        row[0]
        for row in (
            db.query(func.distinct(McpLlmAxisScore.axis_name))
            .order_by(McpLlmAxisScore.axis_name)
            .all()
        )
    ]

    return SummaryResponse(
        total_servers=total,
        scored_servers=scored_count,
        unscored_servers=unscored_count,
        verdict_distribution=sorted(verdict_distribution, key=lambda x: -x.count),
        risk_tier_distribution=sorted(risk_tier_distribution, key=lambda x: -x.count),
        axis_names=axis_names,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# --- Self-test ------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Ensure repo root is on path so `from app.db` resolves correctly
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    # Bypass app/__init__.py (has a broken import of api_router) by using
    # __import__ to reach app.db and app.models directly.
    app_db_mod = __import__("app.db", fromlist=["get_session"])
    _get_session = app_db_mod.get_session
    app_models_mod = __import__("app.models", fromlist=["Base", "McpLlmAxisScore", "McpServerRegistry"])
    Base = app_models_mod.Base
    McpLlmAxisScore = app_models_mod.McpLlmAxisScore
    McpServerRegistry = app_models_mod.McpServerRegistry

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    model_ver = "test-v1"

    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="srv-001", name="Server A",
                registry_source="npm", verdict="trusted", risk_tier="low",
            ),
            McpServerRegistry(
                server_id="srv-002", name="Server B",
                registry_source="github", verdict="trusted", risk_tier="low",
            ),
            McpServerRegistry(
                server_id="srv-003", name="Server C",
                registry_source="npm", verdict="untrusted", risk_tier="high",
            ),
            McpServerRegistry(
                server_id="srv-004", name="Server D",
                registry_source="github", verdict=None, risk_tier=None,
            ),
        ]
        db.add_all(servers)
        db.flush()

        axes_data = [
            ("srv-001", "overall_risk",     "HIGH",     model_ver, now),
            ("srv-001", "auth_strength",    "MEDIUM",   model_ver, now),
            ("srv-002", "overall_risk",     "LOW",      model_ver, now),
            ("srv-002", "auth_strength",    "HIGH",     model_ver, now),
            ("srv-003", "overall_risk",     "CRITICAL", model_ver, now),
        ]
        for sid, axis, label, mv, scored in axes_data:
            db.add(McpLlmAxisScore(
                server_id=sid, axis_name=axis, label=label,
                p_top=0.5, p_critical=0.2, p_danger=0.3,
                model_version=mv, scored_at=scored,
            ))
        db.commit()

    client = TestClient(app)

    # --- Test verdict distribution ---
    resp1 = client.get("/api/verdict_distribution_summary/distribution")
    assert resp1.status_code == 200, f"[verdict-dist] {resp1.status_code}: {resp1.text}"
    d1 = resp1.json()
    assert d1["total_servers"] == 4, d1["total_servers"]
    verdicts = {r["verdict"]: r["count"] for r in d1["distribution"]}
    assert verdicts.get("trusted") == 2, verdicts
    assert verdicts.get("untrusted") == 1, verdicts

    # --- Test risk tier distribution ---
    resp2 = client.get("/api/verdict_distribution_summary/risk-tier")
    assert resp2.status_code == 200, f"[risk-tier] {resp2.status_code}: {resp2.text}"
    d2 = resp2.json()
    assert d2["total_servers"] == 4
    tiers = {r["tier"]: r["count"] for r in d2["tiers"]}
    assert tiers.get("low") == 2, tiers
    assert tiers.get("high") == 1, tiers

    # --- Test server counts ---
    resp3 = client.get("/api/verdict_distribution_summary/server-counts")
    assert resp3.status_code == 200, f"[server-counts] {resp3.status_code}: {resp3.text}"
    d3 = resp3.json()
    assert d3["total_servers"] == 4
    assert d3["scored_servers"] == 3, d3["scored_servers"]
    assert d3["unscored_servers"] == 1, d3["unscored_servers"]

    # --- Test axis distribution ---
    resp4 = client.get("/api/verdict_distribution_summary/axis/overall_risk")
    assert resp4.status_code == 200, f"[axis] {resp4.status_code}: {resp4.text}"
    d4 = resp4.json()
    assert d4["axis_name"] == "overall_risk"
    assert d4["total_servers"] == 3, d4["total_servers"]
    labels = {r["label"]: r["count"] for r in d4["distribution"]}
    assert labels.get("HIGH") == 1, labels
    assert labels.get("LOW") == 1, labels
    assert labels.get("CRITICAL") == 1, labels

    # --- Test summary ---
    resp5 = client.get("/api/verdict_distribution_summary/summary")
    assert resp5.status_code == 200, f"[summary] {resp5.status_code}: {resp5.text}"
    d5 = resp5.json()
    assert d5["total_servers"] == 4
    assert d5["scored_servers"] == 3
    assert d5["unscored_servers"] == 1
    assert len(d5["verdict_distribution"]) > 0
    assert len(d5["risk_tier_distribution"]) > 0
    assert "overall_risk" in d5["axis_names"], d5["axis_names"]
    assert "auth_strength" in d5["axis_names"], d5["axis_names"]

    # --- Test unknown axis returns empty ---
    resp6 = client.get("/api/verdict_distribution_summary/axis/unknown_axis")
    assert resp6.status_code == 200, f"[unknown-axis] {resp6.status_code}: {resp6.text}"
    d6 = resp6.json()
    assert d6["total_servers"] == 0, d6["total_servers"]

    print("PASS")
    sys.exit(0)
