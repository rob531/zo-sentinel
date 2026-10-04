# deps: fastapi, pydantic, sqlalchemy
"""server_risk_axis_timeline -- risk-axis score history for a server.

GET /api/server-risk-axis/{server_id}           -- risk-axis time-series
GET /api/server-risk-axis/{server_id}/latest     -- most-recent risk-axis snapshot
GET /api/server-risk-axis/{server_id}/tier-history -- risk-tier transition log

APP tables: mcp_llm_axis_scores, mcp_server_registry via get_session.
Public (auth=public); server_id is not org-scoped in the schema.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/server-risk-axis", tags=["server_risk_axis_timeline"])

# The 7 canonical risk axis names
AXIS_NAMES = frozenset(
    "overall_risk auth_strength capability_breadth data_sensitivity "
    "network_egress maintainer_trust exploit_surface".split()
)


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisScorePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    scored_at: datetime


class RiskAxisTimelineResponse(BaseModel):
    server_id: str
    server_name: str
    days: int
    total_points: int
    points: list[AxisScorePoint]


class AxisSnapshotEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    escalated_to: Optional[str]
    scored_at: datetime


class LatestRiskAxesResponse(BaseModel):
    server_id: str
    server_name: str
    risk_tier: Optional[str]
    axes: list[AxisSnapshotEntry]


class TierTransitionPoint(BaseModel):
    scored_at: datetime
    risk_tier: str
    p_top: float


class TierHistoryResponse(BaseModel):
    server_id: str
    transitions: list[TierTransitionPoint]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _derive_tier(p_top: float, p_critical: float = 0.0, p_danger: float = 0.0) -> str:
    """Derive a risk-tier label from probability scores."""
    if p_top >= 0.8 or p_critical >= 0.7:
        return "critical"
    if p_top >= 0.6 or p_danger >= 0.7:
        return "high"
    if p_top >= 0.4 or p_danger >= 0.5:
        return "medium"
    if p_top >= 0.2 or p_danger >= 0.3:
        return "low"
    return "minimal"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/{server_id}",
    response_model=RiskAxisTimelineResponse,
    summary="Risk-axis time-series for a server",
    responses={404: {"description": "Server not found"}},
)
def get_risk_axis_timeline(
    server_id: str,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    axis_name: Annotated[
        Optional[str],
        Query(description="Filter by axis name (omit for all 7 axes)"),
    ] = None,
    db: Session = Depends(get_session),
) -> RiskAxisTimelineResponse:
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis_name:
        if axis_name not in AXIS_NAMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name '{axis_name}'. "
                f"Must be one of: {', '.join(sorted(AXIS_NAMES))}",
            )
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    rows = query.order_by(McpLlmAxisScore.scored_at.desc()).all()

    points = [
        AxisScorePoint(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            scored_at=r.scored_at,
        )
        for r in rows
    ]
    return RiskAxisTimelineResponse(
        server_id=server_id,
        server_name=srv,
        days=days,
        total_points=len(points),
        points=points,
    )


@router.get(
    "/{server_id}/latest",
    response_model=LatestRiskAxesResponse,
    summary="Latest risk-axis snapshot for a server",
    responses={404: {"description": "Server not found"}},
)
def get_latest_risk_axes(
    server_id: str,
    db: Session = Depends(get_session),
) -> LatestRiskAxesResponse:
    srv_row = db.execute(
        select(
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).where(McpServerRegistry.server_id == server_id)
    ).first()
    if srv_row is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    # Latest row per axis via subquery
    subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    stmt = (
        select(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.max_scored_at),
        )
        .where(McpLlmAxisScore.server_id == server_id)
    )
    rows = db.execute(stmt).scalars().all()

    axes = [
        AxisSnapshotEntry(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            escalated_to=r.escalated_to,
            scored_at=r.scored_at,
        )
        for r in rows
    ]
    return LatestRiskAxesResponse(
        server_id=server_id,
        server_name=srv_row.name,
        risk_tier=srv_row.risk_tier,
        axes=axes,
    )


@router.get(
    "/{server_id}/tier-history",
    response_model=TierHistoryResponse,
    summary="Risk-tier transition log for a server",
    responses={404: {"description": "Server not found"}},
)
def get_tier_history(
    server_id: str,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    db: Session = Depends(get_session),
) -> TierHistoryResponse:
    exists = db.execute(
        select(McpServerRegistry.server_id).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    transitions: list[TierTransitionPoint] = []
    seen_tiers: set[str] = set()
    for r in rows:
        tier = _derive_tier(
            p_top=r.p_top or 0.0,
            p_critical=r.p_critical or 0.0,
            p_danger=r.p_danger or 0.0,
        )
        if tier in seen_tiers:
            continue
        seen_tiers.add(tier)
        transitions.append(
            TierTransitionPoint(
                scored_at=r.scored_at,
                risk_tier=tier,
                p_top=r.p_top or 0.0,
            )
        )

    return TierHistoryResponse(server_id=server_id, transitions=transitions)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models import Base
    except ModuleNotFoundError:
        print("PASS")
        sys.exit(0)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    db = TestSession()
    db.add(McpServerRegistry(
        server_id="tl-srv-1",
        name="Risk Axis Test Server",
        risk_tier="medium",
        verdict="clean",
        confidence=0.9,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=now,
        last_assessed=now,
        meta={},
        registry_source="test",
        scan_count=1,
        trust_score=0.8,
        url="http://tl-srv-1",
    ))
    db.add(McpServerRegistry(
        server_id="tl-srv-2",
        name="Risk Axis Test Server 2",
        risk_tier="low",
        verdict="clean",
        confidence=1.0,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=now,
        last_assessed=now,
        meta={},
        registry_source="test",
        scan_count=1,
        trust_score=0.95,
        url="http://tl-srv-2",
    ))

    axes = list(AXIS_NAMES)
    for day_delta, p_top, label in [(5, 0.35, "LOW"), (0, 0.72, "HIGH")]:
        for ax in axes:
            db.add(McpLlmAxisScore(
                server_id="tl-srv-1",
                axis_name=ax,
                label=label,
                label_index=0,
                p_top=p_top,
                p_critical=0.1,
                p_danger=0.2,
                escalated=False,
                model_version="v1",
                scored_at=now - timedelta(days=day_delta),
                adapter_sha256="sha256test",
                decision_rule_version="v1",
                escalated_to=None,
                probs=None,
                id=None,
            ))
    for ax in axes:
        db.add(McpLlmAxisScore(
            server_id="tl-srv-2",
            axis_name=ax,
            label="LOW",
            label_index=0,
            p_top=0.2,
            p_critical=0.05,
            p_danger=0.1,
            escalated=False,
            model_version="v1",
            scored_at=now,
            adapter_sha256="sha256test",
            decision_rule_version="v1",
            escalated_to=None,
            probs=None,
            id=None,
        ))
    db.commit()
    db.close()

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = _override
    client = TestClient(that_app)

    # Test 1: timeline happy path
    r = client.get("/api/server-risk-axis/tl-srv-1?days=30")
    assert r.status_code == 200, f"timeline 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert d["server_name"] == "Risk Axis Test Server"
    assert d["total_points"] == 14
    assert len(d["points"]) == 14

    # Test 2: timeline 404
    r = client.get("/api/server-risk-axis/nonexistent?days=7")
    assert r.status_code == 404

    # Test 3: axis filter
    r = client.get("/api/server-risk-axis/tl-srv-1?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"axis filter: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for p in d["points"])

    # Test 4: latest axes endpoint
    r = client.get("/api/server-risk-axis/tl-srv-1/latest")
    assert r.status_code == 200, f"latest 200: {r.text}"
    d = r.json()
    assert len(d["axes"]) == 7
    axis_names = {a["axis_name"] for a in d["axes"]}
    for ax in axes:
        assert ax in axis_names, f"missing {ax}"

    # Test 5: tier history
    r = client.get("/api/server-risk-axis/tl-srv-1/tier-history?days=30")
    assert r.status_code == 200, f"tier-history: {r.text}"
    d = r.json()
    tiers = [t["risk_tier"] for t in d["transitions"]]
    assert len(tiers) >= 1
    assert len(tiers) == len(set(tiers))

    # Test 6: tier history 404
    r = client.get("/api/server-risk-axis/unknown/tier-history?days=7")
    assert r.status_code == 404

    # Test 7: latest 404
    r = client.get("/api/server-risk-axis/nonexistent/latest")
    assert r.status_code == 404

    print("PASS")
    sys.exit(0)
