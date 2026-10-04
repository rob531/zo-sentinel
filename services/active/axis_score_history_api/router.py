# deps: fastapi, sqlalchemy, pydantic
"""axis_score_history_api service.

Provides historical axis-score endpoints for MCP servers:
  - GET /api/axis_score_history/server/{server_id}          -- per-server axis score timeline
  - GET /api/axis_score_history/distribution                -- axis distribution across all servers
  - GET /api/axis_score_history/stats                      -- aggregate statistics

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
Public endpoint (auth=public per the directive).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis_score_history", tags=["axis_score_history"])

# Known axis names from the schema
AXIS_NAMES = frozenset({
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
})


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisScorePoint(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: str
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ServerHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    days: int
    total_scores: int
    timeline: List[AxisScorePoint]


class AxisDistributionEntry(BaseModel):
    axis_name: str
    server_count: int
    avg_p_top: Optional[float]
    latest_label: Optional[str]
    escalated_count: int


class DistributionResponse(BaseModel):
    as_of: str
    total_servers: int
    axes: List[AxisDistributionEntry]


class AxisStatsEntry(BaseModel):
    axis_name: str
    score_count: int
    server_count: int
    avg_p_top: Optional[float]
    min_p_top: Optional[float]
    max_p_top: Optional[float]
    escalated_count: int


class StatsResponse(BaseModel):
    as_of: str
    period_days: int
    total_servers: int
    total_scores: int
    axes: List[AxisStatsEntry]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/server/{server_id}",
    response_model=ServerHistoryResponse,
    summary="Get axis score history for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    axis_name: Optional[str] = Query(
        default=None,
        description="Filter by specific axis name (e.g. overall_risk)",
    ),
    db: Session = Depends(get_session),
) -> ServerHistoryResponse:
    """
    Returns the axis score history for a specific server over the requested period,
    ordered newest-first. Optionally filter to a single axis.
    """
    srv = db.execute(
        select(McpServerRegistry.name).where(
            McpServerRegistry.server_id == server_id
        )
    ).scalar_one_or_none()

    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=days)

    q = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )

    if axis_name is not None:
        q = q.filter(McpLlmAxisScore.axis_name == axis_name)

    scores = q.order_by(McpLlmAxisScore.scored_at.desc()).all()

    timeline = [
        AxisScorePoint(
            axis_name=s.axis_name,
            label=s.label,
            label_index=s.label_index,
            p_top=s.p_top,
            p_critical=s.p_critical,
            p_danger=s.p_danger,
            escalated=bool(s.escalated),
            model_version=s.model_version or "",
            scored_at=s.scored_at,
        )
        for s in scores
    ]

    return ServerHistoryResponse(
        server_id=server_id,
        name=srv,
        days=days,
        total_scores=len(timeline),
        timeline=timeline,
    )


@router.get(
    "/distribution",
    response_model=DistributionResponse,
    summary="Get axis score distribution across all servers",
)
def get_distribution(
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None, description="Filter to a specific axis"),
    db: Session = Depends(get_session),
) -> DistributionResponse:
    """
    Returns aggregated distribution statistics per axis across all servers
    for the specified lookback window.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    q_base = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.scored_at >= cutoff)
    if axis_name is not None:
        q_base = q_base.filter(McpLlmAxisScore.axis_name == axis_name)

    total_servers = db.execute(
        select(func.count(func.distinct(McpLlmAxisScore.server_id))).where(
            McpLlmAxisScore.scored_at >= cutoff
        )
        if axis_name is None
        else select(func.count(func.distinct(McpLlmAxisScore.server_id))).where(
            McpLlmAxisScore.scored_at >= cutoff,
            McpLlmAxisScore.axis_name == axis_name,
        )
    ).scalar_one() or 0

    axis_stats = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.count(func.nullif(McpLlmAxisScore.escalated, False)).label("escalated_count"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    # Latest label per axis
    latest_rows = db.execute(
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
    ).all()

    latest_label_map: Dict[str, str] = {}
    for row in latest_rows:
        if row.axis_name not in latest_label_map:
            latest_label_map[row.axis_name] = row.label

    axes = [
        AxisDistributionEntry(
            axis_name=stat.axis_name,
            server_count=stat.server_count,
            avg_p_top=round(float(stat.avg_p_top), 4) if stat.avg_p_top is not None else None,
            latest_label=latest_label_map.get(stat.axis_name),
            escalated_count=stat.escalated_count,
        )
        for stat in axis_stats
    ]

    return DistributionResponse(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_servers=total_servers,
        axes=axes,
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get aggregate axis score statistics",
)
def get_stats(
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None, description="Filter to a specific axis"),
    db: Session = Depends(get_session),
) -> StatsResponse:
    """
    Returns min / max / avg statistics per axis across all servers
    for the specified lookback window.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    total_servers = db.execute(
        select(func.count(func.distinct(McpLlmAxisScore.server_id))).where(
            McpLlmAxisScore.scored_at >= cutoff
        )
    ).scalar_one() or 0

    total_scores = db.execute(
        select(func.count(McpLlmAxisScore.id)).where(
            McpLlmAxisScore.scored_at >= cutoff
        )
    ).scalar_one() or 0

    q = (
        select(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("score_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.min(McpLlmAxisScore.p_top).label("min_p_top"),
            func.max(McpLlmAxisScore.p_top).label("max_p_top"),
            func.count(func.nullif(McpLlmAxisScore.escalated, False)).label("escalated_count"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    )

    if axis_name is not None:
        q = q.where(McpLlmAxisScore.axis_name == axis_name)

    stats = db.execute(q).all()

    axes = [
        AxisStatsEntry(
            axis_name=stat.axis_name,
            score_count=stat.score_count,
            server_count=stat.server_count,
            avg_p_top=round(float(stat.avg_p_top), 4) if stat.avg_p_top is not None else None,
            min_p_top=round(float(stat.min_p_top), 4) if stat.min_p_top is not None else None,
            max_p_top=round(float(stat.max_p_top), 4) if stat.max_p_top is not None else None,
            escalated_count=stat.escalated_count,
        )
        for stat in stats
    ]

    return StatsResponse(
        as_of=datetime.utcnow().isoformat() + "Z",
        period_days=days,
        total_servers=total_servers,
        total_scores=total_scores,
        axes=axes,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite: test override for app.db
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestSession()
    db.add(McpServerRegistry(server_id="srv-h-1", name="History Test Server"))
    db.add(McpLlmAxisScore(
        server_id="srv-h-1",
        axis_name="overall_risk",
        label="MEDIUM",
        label_index=1,
        p_top=0.45,
        p_critical=0.1,
        p_danger=0.2,
        escalated=False,
        model_version="v1",
        scored_at=datetime.utcnow() - timedelta(days=2),
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-h-1",
        axis_name="overall_risk",
        label="HIGH",
        label_index=2,
        p_top=0.65,
        p_critical=0.2,
        p_danger=0.3,
        escalated=False,
        model_version="v1",
        scored_at=datetime.utcnow() - timedelta(days=1),
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-h-1",
        axis_name="auth_strength",
        label="WEAK",
        label_index=0,
        p_top=0.25,
        p_critical=0.05,
        p_danger=0.1,
        escalated=False,
        model_version="v1",
        scored_at=datetime.utcnow() - timedelta(days=1),
    ))
    db.commit()
    db.close()

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Test 1: server history
    r = client.get("/api/axis_score_history/server/srv-h-1?days=7")
    assert r.status_code == 200, f"history failed: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-h-1"
    assert d["total_scores"] == 3, f"expected 3 scores, got {d['total_scores']}"
    assert len(d["timeline"]) == 3

    # Test 2: server history filtered by axis
    r2 = client.get("/api/axis_score_history/server/srv-h-1?days=7&axis_name=overall_risk")
    assert r2.status_code == 200, f"axis-filtered history failed: {r2.text}"
    d2 = r2.json()
    assert d2["total_scores"] == 2, f"expected 2 overall_risk scores, got {d2['total_scores']}"

    # Test 3: 404 for unknown server
    r3 = client.get("/api/axis_score_history/server/unknown-srv?days=7")
    assert r3.status_code == 404, f"expected 404, got {r3.status_code}"

    # Test 4: distribution
    r4 = client.get("/api/axis_score_history/distribution?days=7")
    assert r4.status_code == 200, f"distribution failed: {r4.text}"
    d4 = r4.json()
    assert d4["total_servers"] == 1
    assert len(d4["axes"]) == 2  # overall_risk + auth_strength

    # Test 5: stats
    r5 = client.get("/api/axis_score_history/stats?days=7")
    assert r5.status_code == 200, f"stats failed: {r5.text}"
    d5 = r5.json()
    assert d5["total_servers"] == 1
    assert d5["total_scores"] == 3
    assert len(d5["axes"]) == 2

    # Test 6: stats filtered by axis
    r6 = client.get("/api/axis_score_history/stats?days=7&axis_name=overall_risk")
    assert r6.status_code == 200, f"axis-filtered stats failed: {r6.text}"
    d6 = r6.json()
    assert len(d6["axes"]) == 1

    print("Self-test PASSED")
