# deps: fastapi, pydantic, sqlalchemy
"""Server Risk Ranking Service.

Provides endpoints to rank MCP servers by computed risk score derived from
LLM axis scores (p_top, p_critical, p_danger) and registry metadata.

Public endpoint -- no authentication required.
Data: app Postgres via get_session + SQLAlchemy ORM on mcp_server_registry /
      mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_ranking"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AxisScoreEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    scored_at: Optional[datetime] = None


class ServerRiskProfile(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    composite_score: float
    risk_label: str  # CRITICAL | HIGH | MEDIUM | LOW
    axes: List[AxisScoreEntry]
    last_assessed: Optional[str] = None


class RiskRankingResponse(BaseModel):
    generated_at: str
    total_servers: int
    ranked_servers: List[ServerRiskProfile]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RISK_THRESHOLDS = [
    (0.50, "CRITICAL"),
    (0.30, "HIGH"),
    (0.15, "MEDIUM"),
    (0.00, "LOW"),
]


def _risk_label(p_critical: Optional[float], p_danger: Optional[float]) -> str:
    """Derive a risk label from p_critical and p_danger probabilities."""
    pc = p_critical if p_critical is not None else 0.0
    pd = p_danger if p_danger is not None else 0.0
    val = max(pc, pd)
    for threshold, label in RISK_THRESHOLDS:
        if val >= threshold:
            return label
    return "LOW"


def _composite_score(p_top: Optional[float], p_critical: Optional[float], p_danger: Optional[float]) -> float:
    """Compute a weighted composite score from axis probabilities."""
    pt = p_top if p_top is not None else 0.0
    pc = p_critical if p_critical is not None else 0.0
    pd = p_danger if p_danger is not None else 0.0
    # Weighted: p_top (lower is better) inverted, penalise critical/danger
    return round(max(pt - 0.5 * pc - 0.3 * pd, 0.0), 4)


def _build_profile(server_id: str, axes: List[McpLlmAxisScore], srv: Optional[McpServerRegistry]) -> ServerRiskProfile:
    """Assemble a ServerRiskProfile from a list of axis scores."""
    entries = [
        AxisScoreEntry(
            axis_name=a.axis_name,
            label=a.label or "UNKNOWN",
            p_top=a.p_top,
            p_critical=a.p_critical,
            p_danger=a.p_danger,
            scored_at=a.scored_at,
        )
        for a in axes
    ]

    # Overall composite from overall_risk axis if present
    overall = next((a for a in axes if a.axis_name == "overall_risk"), None)
    if overall:
        composite = _composite_score(overall.p_top, overall.p_critical, overall.p_danger)
        risk_lbl = _risk_label(overall.p_critical, overall.p_danger)
    else:
        composite = 0.0
        risk_lbl = "LOW"

    last_assessed: Optional[str] = None
    if overall and overall.scored_at:
        last_assessed = overall.scored_at.isoformat()
    elif srv and srv.last_assessed:
        last_assessed = srv.last_assessed.isoformat()

    return ServerRiskProfile(
        server_id=server_id,
        name=srv.name if srv else None,
        registry_source=srv.registry_source if srv else None,
        risk_tier=srv.risk_tier if srv else None,
        verdict=srv.verdict if srv else None,
        composite_score=composite,
        risk_label=risk_lbl,
        axes=entries,
        last_assessed=last_assessed,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/risk_ranking", response_model=RiskRankingResponse)
def get_risk_ranking(
    limit: int = Query(default=100, ge=1, le=500, description="Max servers to return"),
    axis_name: str = Query(default="overall_risk", description="Axis to rank by"),
    direction: str = Query(default="asc", description="Sort direction: asc (highest risk first) or desc"),
    db: Session = Depends(get_session),
) -> RiskRankingResponse:
    """
    Return servers ranked by risk, most-risky first by default.
    Sorting uses p_critical desc / p_danger desc so CRITICAL servers appear first.
    """
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Subquery: latest model version per server
    subq_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Latest score per server for the chosen axis
    axis_rows = (
        db.query(McpLlmAxisScore)
        .join(
            subq_latest,
            (McpLlmAxisScore.server_id == subq_latest.c.server_id)
            & (McpLlmAxisScore.scored_at == subq_latest.c.latest_at)
            & (McpLlmAxisScore.axis_name == axis_name),
        )
        .all()
    )

    axis_by_server: dict[str, McpLlmAxisScore] = {r.server_id: r for r in axis_rows}
    server_ids = list(axis_by_server.keys())

    servers: dict[str, McpServerRegistry] = {}
    if server_ids:
        for srv in db.query(McpServerRegistry).filter(McpServerRegistry.server_id.in_(server_ids)).all():
            servers[srv.server_id] = srv

    # Build profiles for servers that have axis scores
    profiles: List[ServerRiskProfile] = []
    for sid in server_ids:
        # Fetch all axes for this server (latest version)
        srv_subq = (
            db.query(
                McpLlmAxisScore.server_id,
                func.max(McpLlmAxisScore.scored_at).label("latest_at"),
            )
            .filter(McpLlmAxisScore.server_id == sid)
            .group_by(McpLlmAxisScore.server_id)
            .subquery()
        )
        all_axes = (
            db.query(McpLlmAxisScore)
            .join(
                srv_subq,
                (McpLlmAxisScore.server_id == srv_subq.c.server_id)
                & (McpLlmAxisScore.scored_at == srv_subq.c.latest_at),
            )
            .all()
        )
        srv_obj = servers.get(sid)
        profiles.append(_build_profile(sid, all_axes, srv_obj))

    # Sort: highest risk first (highest p_critical + p_danger)
    reverse = direction == "desc"
    profiles.sort(
        key=lambda p: (
            -_risk_label(
                next((a.p_critical for a in p.axes if a.axis_name == axis_name), None),
                next((a.p_danger for a in p.axes if a.axis_name == axis_name), None),
            )
            != "LOW",
            -(
                max(
                    next((a.p_critical or 0 for a in p.axes if a.axis_name == axis_name), 0),
                    next((a.p_danger or 0 for a in p.axes if a.axis_name == axis_name), 0),
                )
            ),
        ),
        reverse=reverse,
    )

    return RiskRankingResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_servers=total,
        ranked_servers=profiles[:limit],
    )


@router.get("/risk_ranking/server/{server_id}", response_model=ServerRiskProfile)
def get_server_risk_profile(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerRiskProfile:
    """Return the full risk profile for a single server."""
    # Get all latest axis scores for this server
    subq_latest = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    axes = (
        db.query(McpLlmAxisScore)
        .join(
            subq_latest,
            (McpLlmAxisScore.server_id == server_id)
            & (McpLlmAxisScore.axis_name == subq_latest.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq_latest.c.latest_at),
        )
        .all()
    )

    srv = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()

    return _build_profile(server_id, axes, srv)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

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

    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="srv-1",
                name="Safe Server",
                registry_source="npm",
                risk_tier="TRUSTED_GENERAL",
                verdict="safe",
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="Risky Server",
                registry_source="github",
                risk_tier="HIGH_RISK_ISOLATED",
                verdict="dangerous",
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="srv-3",
                name="Unknown Server",
                registry_source="npm",
                risk_tier=None,
                verdict=None,
                last_assessed=now,
            ),
        ]
        db.add_all(servers)
        db.flush()

        # srv-1: low risk
        for axis, p_top, p_crit, p_dang in [
            ("overall_risk",    0.05, 0.02, 0.03),
            ("auth_strength",   0.80, 0.05, 0.05),
            ("capability_breadth", 0.60, 0.10, 0.10),
        ]:
            db.add(McpLlmAxisScore(
                server_id="srv-1", axis_name=axis,
                label="LOW", label_index=0,
                p_top=p_top, p_critical=p_crit, p_danger=p_dang,
                model_version="v1", scored_at=now,
            ))

        # srv-2: critical risk
        for axis, p_top, p_crit, p_dang in [
            ("overall_risk",    0.95, 0.55, 0.70),
            ("auth_strength",   0.10, 0.40, 0.50),
            ("capability_breadth", 0.90, 0.30, 0.40),
        ]:
            db.add(McpLlmAxisScore(
                server_id="srv-2", axis_name=axis,
                label="CRITICAL", label_index=3,
                p_top=p_top, p_critical=p_crit, p_danger=p_dang,
                model_version="v1", scored_at=now,
            ))

        # srv-3: no scores
        db.commit()

    client = TestClient(app)

    # --- Test ranked list ---
    resp1 = client.get("/api/risk_ranking", params={"limit": 10})
    assert resp1.status_code == 200, f"ranked: {resp1.status_code}: {resp1.text}"
    d1 = resp1.json()
    assert d1["total_servers"] == 3, d1["total_servers"]
    assert len(d1["ranked_servers"]) == 2, f"Expected 2 scored servers, got {len(d1['ranked_servers'])}"
    # srv-2 should be first (highest risk)
    ranked_ids = [r["server_id"] for r in d1["ranked_servers"]]
    assert ranked_ids[0] == "srv-2", f"srv-2 should be first, got {ranked_ids}"
    assert ranked_ids[1] == "srv-1", f"srv-1 should be second, got {ranked_ids}"

    # --- Test srv-2 is CRITICAL ---
    srv2 = next(r for r in d1["ranked_servers"] if r["server_id"] == "srv-2")
    assert srv2["risk_label"] == "CRITICAL", srv2["risk_label"]

    # --- Test srv-1 is LOW ---
    srv1 = next(r for r in d1["ranked_servers"] if r["server_id"] == "srv-1")
    assert srv1["risk_label"] == "LOW", srv1["risk_label"]

    # --- Test per-server endpoint ---
    resp2 = client.get("/api/risk_ranking/server/srv-2")
    assert resp2.status_code == 200, f"server: {resp2.status_code}: {resp2.text}"
    d2 = resp2.json()
    assert d2["server_id"] == "srv-2"
    assert d2["risk_label"] == "CRITICAL"
    assert len(d2["axes"]) == 3, len(d2["axes"])

    # --- Test 404 for unscored server ---
    resp3 = client.get("/api/risk_ranking/server/srv-3")
    assert resp3.status_code == 200, f"srv-3 should return 200 (no scores, LOW): {resp3.status_code}"
    d3 = resp3.json()
    assert d3["risk_label"] == "LOW"

    # --- Test limit ---
    resp4 = client.get("/api/risk_ranking", params={"limit": 1})
    assert resp4.status_code == 200
    d4 = resp4.json()
    assert len(d4["ranked_servers"]) == 1, len(d4["ranked_servers"])

    print("PASS")
    sys.exit(0)
