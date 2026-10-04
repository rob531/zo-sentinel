# deps: fastapi, sqlalchemy, requests
"""mcp_llm_axis_scores_timeline service.

Provides timeline and distribution endpoints for LLM axis scores:
  - GET /api/mcp_llm_axis_scores_timeline/server/{server_id}  -- per-server axis score timeline
  - GET /api/mcp_llm_axis_scores_timeline/distribution         -- overall axis distribution

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint — no auth required.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/mcp_llm_axis_scores_timeline", tags=["mcp_llm_axis_scores_timeline"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisScoreEntry(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: str
    scored_at: datetime


class ServerTimelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str]
    days: int
    total_scores: int
    timeline: List[AxisScoreEntry]


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


class SignalTimelinePoint(BaseModel):
    day: str
    axis_name: str
    avg_score: Optional[float]
    count: int


class SignalTimelineResponse(BaseModel):
    days: int
    points: List[SignalTimelinePoint]


# --------------------------------------------------------------------------- #
# Mesh helpers
# --------------------------------------------------------------------------- #

def _query_mesh(sql: str, params: Optional[dict] = None) -> list[dict]:
    """Read-only query against the ZoComputer mesh store."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql, "params": params or {}},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Mesh query failed: {exc}")
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=502, detail=data["error"])
    if not isinstance(data, list):
        return []
    return data


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/server/{server_id}",
    response_model=ServerTimelineResponse,
    summary="Get LLM axis score timeline for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ServerTimelineResponse:
    """
    Returns the axis score history for a specific server over the requested period.
    """
    # Verify server exists
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    timeline = [
        AxisScoreEntry(
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

    return ServerTimelineResponse(
        server_id=server_id,
        name=srv,
        days=days,
        total_scores=len(timeline),
        timeline=timeline,
    )


@router.get(
    "/distribution",
    response_model=DistributionResponse,
    summary="Get distribution of axis scores across all servers",
)
def get_axis_distribution(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> DistributionResponse:
    """
    Returns aggregated axis score distribution statistics across all servers.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Total unique servers with scores in period
    total_servers = db.execute(
        select(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .where(McpLlmAxisScore.scored_at >= cutoff)
    ).scalar_one() or 0

    # Group by axis_name
    axis_stats = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.count(
                func.nullif(McpLlmAxisScore.escalated, False)
            ).label("escalated_count"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    # Get latest label per axis
    latest_labels = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
    ).all()

    latest_label_map: Dict[str, str] = {}
    for row in latest_labels:
        axis = row.axis_name
        if axis not in latest_label_map:
            latest_label_map[axis] = row.label

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
    "/signal-timeline",
    response_model=SignalTimelineResponse,
    summary="Get signal score timeline from mesh store",
)
def get_signal_timeline(
    axis_name: Optional[str] = Query(default=None, description="Filter by axis name"),
    days: int = Query(default=30, ge=1, le=365),
) -> SignalTimelineResponse:
    """
    Returns time-series of signal scores from the mesh store.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    if axis_name:
        rows = _query_mesh(
            """
            SELECT
                DATE(scored_at) AS day,
                axis_name,
                AVG(score) AS avg_score,
                COUNT(*)   AS cnt
            FROM mcp_signal_scores
            WHERE axis_name = :axis_name
              AND DATE(scored_at) >= :cutoff
            GROUP BY DATE(scored_at), axis_name
            ORDER BY day ASC
            """,
            {"axis_name": axis_name, "cutoff": cutoff},
        )
    else:
        rows = _query_mesh(
            """
            SELECT
                DATE(scored_at) AS day,
                axis_name,
                AVG(score) AS avg_score,
                COUNT(*)   AS cnt
            FROM mcp_signal_scores
            WHERE DATE(scored_at) >= :cutoff
            GROUP BY DATE(scored_at), axis_name
            ORDER BY day ASC, axis_name
            """,
            {"cutoff": cutoff},
        )

    points = [
        SignalTimelinePoint(
            day=r.get("day", ""),
            axis_name=r.get("axis_name", ""),
            avg_score=round(r.get("avg_score"), 4) if r.get("avg_score") is not None else None,
            count=r.get("cnt") or 0,
        )
        for r in rows
    ]

    return SignalTimelineResponse(days=days, points=points)


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
    db.add(McpServerRegistry(server_id="srv-tl-1", name="Timeline Test Server"))
    db.add(McpLlmAxisScore(
        server_id="srv-tl-1",
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
        server_id="srv-tl-1",
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
        server_id="srv-tl-1",
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

    # Test 1: server timeline
    r = client.get("/api/mcp_llm_axis_scores_timeline/server/srv-tl-1?days=7")
    assert r.status_code == 200, f"timeline failed: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-tl-1"
    assert d["total_scores"] == 3
    assert len(d["timeline"]) == 3

    # Test 2: distribution
    r2 = client.get("/api/mcp_llm_axis_scores_timeline/distribution?days=7")
    assert r2.status_code == 200, f"distribution failed: {r2.text}"
    d2 = r2.json()
    assert d2["total_servers"] == 1
    assert len(d2["axes"]) == 2

    # Test 3: 404 for unknown server
    r3 = client.get("/api/mcp_llm_axis_scores_timeline/server/unknown-srv?days=7")
    assert r3.status_code == 404, f"expected 404, got {r3.status_code}"

    # Test 4: signal timeline (empty mesh)
    r4 = client.get("/api/mcp_llm_axis_scores_timeline/signal-timeline?days=7")
    assert r4.status_code == 200, f"signal-timeline failed: {r4.text}"
    d4 = r4.json()
    assert "points" in d4

    print("Self-test PASSED")
