# deps: fastapi, pydantic, sqlalchemy, requests
"""axis_score_time_series -- time-series of LLM risk-axis scores across the MCP fleet.

Endpoints
  GET /api/axis-score-time-series/server/{server_id}   -- per-server axis score time-series
  GET /api/axis-score-time-series/distribution           -- aggregate axis distribution across all servers
  GET /api/axis-score-time-series/axis/{axis_name}      -- daily trend for a specific axis
  GET /api/axis-score-time-series/signal-timeline       -- signal scores from mesh store

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint (auth=public).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis-score-time-series", tags=["axis_score_time_series"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

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

class AxisPoint(BaseModel):
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


class ServerTimeSeriesResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    days: int
    total_points: int
    points: list[AxisPoint]


class DistributionAxisEntry(BaseModel):
    axis_name: str
    server_count: int
    score_count: int
    avg_p_top: Optional[float]
    escalated_count: int


class DistributionResponse(BaseModel):
    as_of: str
    total_servers: int
    axes: list[DistributionAxisEntry]


class TrendPoint(BaseModel):
    day: str
    score_count: int
    avg_p_top: Optional[float]
    avg_p_critical: Optional[float]
    avg_p_danger: Optional[float]
    escalated_count: int


class AxisTrendResponse(BaseModel):
    axis_name: str
    days: int
    points: list[TrendPoint]


class SignalPoint(BaseModel):
    day: str
    axis_name: str
    avg_score: Optional[float]
    count: int


class SignalTimelineResponse(BaseModel):
    days: int
    points: list[SignalPoint]


# --------------------------------------------------------------------------- #
# Mesh helper
# --------------------------------------------------------------------------- #

def _query_mesh(sql: str, params: Optional[dict] = None) -> list[dict]:
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
    response_model=ServerTimeSeriesResponse,
    summary="Per-server axis score time-series",
    responses={404: {"description": "Server not found"}},
)
def get_server_time_series(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None, description="Filter to one axis"),
    db: Session = Depends(get_session),
) -> ServerTimeSeriesResponse:
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
            raise HTTPException(status_code=400, detail=f"Invalid axis_name '{axis_name}'")
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = query.order_by(McpLlmAxisScore.scored_at.desc()).all()

    points = [
        AxisPoint(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            model_version=r.model_version or "",
            scored_at=r.scored_at,
        )
        for r in rows
    ]
    return ServerTimeSeriesResponse(
        server_id=server_id,
        server_name=srv,
        days=days,
        total_points=len(points),
        points=points,
    )


@router.get(
    "/distribution",
    response_model=DistributionResponse,
    summary="Aggregate axis distribution across all servers",
)
def get_distribution(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> DistributionResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    total_servers = db.execute(
        select(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .where(McpLlmAxisScore.scored_at >= cutoff)
    ).scalar_one() or 0

    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.count().label("score_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.sum(func.cast(McpLlmAxisScore.escalated, int)).label("esc_count"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    axes = [
        DistributionAxisEntry(
            axis_name=row.axis_name,
            server_count=row.server_count or 0,
            score_count=row.score_count or 0,
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else None,
            escalated_count=row.esc_count or 0,
        )
        for row in rows
    ]
    return DistributionResponse(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_servers=total_servers,
        axes=axes,
    )


@router.get(
    "/axis/{axis_name}",
    response_model=AxisTrendResponse,
    summary="Daily trend for a specific axis",
    responses={400: {"description": "Invalid axis_name"}},
)
def get_axis_trend(
    axis_name: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisTrendResponse:
    if axis_name not in AXIS_NAMES:
        raise HTTPException(status_code=400, detail=f"Invalid axis_name '{axis_name}'")

    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = db.execute(
        select(
            func.date(McpLlmAxisScore.scored_at).label("day"),
            func.count().label("cnt"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.sum(func.cast(McpLlmAxisScore.escalated, int)).label("esc_cnt"),
        )
        .where(McpLlmAxisScore.axis_name == axis_name)
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(func.date(McpLlmAxisScore.scored_at))
        .order_by(func.date(McpLlmAxisScore.scored_at))
    ).all()

    points = [
        TrendPoint(
            day=str(row.day),
            score_count=row.cnt or 0,
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else None,
            avg_p_critical=round(float(row.avg_p_critical), 4) if row.avg_p_critical is not None else None,
            avg_p_danger=round(float(row.avg_p_danger), 4) if row.avg_p_danger is not None else None,
            escalated_count=row.esc_cnt or 0,
        )
        for row in rows
    ]
    return AxisTrendResponse(axis_name=axis_name, days=days, points=points)


@router.get(
    "/signal-timeline",
    response_model=SignalTimelineResponse,
    summary="Signal score timeline from mesh store",
)
def get_signal_timeline(
    axis_name: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
) -> SignalTimelineResponse:
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
        SignalPoint(
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
        server_id="ats-srv-1", name="ATS Server 1", registry_source="test",
        url="http://ats1", description="test", trust_score=0.8, verdict="clean",
        confidence=0.9, last_assessed=now, first_seen=now, last_seen=now,
        last_scanned=now, scan_count=1, risk_tier="medium", meta={},
    ))
    db.add(McpServerRegistry(
        server_id="ats-srv-2", name="ATS Server 2", registry_source="test",
        url="http://ats2", description="test", trust_score=0.9, verdict="clean",
        confidence=1.0, last_assessed=now, first_seen=now, last_seen=now,
        last_scanned=now, scan_count=1, risk_tier="low", meta={},
    ))

    axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]
    for day_delta, p_top, label in [(5, 0.35, "LOW"), (0, 0.72, "HIGH")]:
        for ax in axes:
            db.add(McpLlmAxisScore(
                server_id="ats-srv-1", axis_name=ax, label=label, label_index=0,
                p_top=p_top, p_critical=0.1, p_danger=0.2, escalated=False,
                model_version="v1", scored_at=now - timedelta(days=day_delta),
                adapter_sha256="sha256test", decision_rule_version="v1",
                escalated_to=None, probs=None,
            ))
    for ax in axes:
        db.add(McpLlmAxisScore(
            server_id="ats-srv-2", axis_name=ax, label="LOW", label_index=0,
            p_top=0.2, p_critical=0.05, p_danger=0.1, escalated=False,
            model_version="v1", scored_at=now,
            adapter_sha256="sha256test", decision_rule_version="v1",
            escalated_to=None, probs=None, id=None,
        ))
    db.commit()
    db.close()

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    client = TestClient(test_app)
    client.app.dependency_overrides[get_session] = _override

    # Test 1: server time-series — happy path
    r = client.get("/api/axis-score-time-series/server/ats-srv-1?days=30")
    assert r.status_code == 200, f"server ts 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "ats-srv-1"
    assert d["server_name"] == "ATS Server 1"
    assert d["total_points"] == 14, f"expected 14 points (7 axes x 2 rounds), got {d['total_points']}"
    assert len(d["points"]) == 14

    # Test 2: server time-series — 404
    r = client.get("/api/axis-score-time-series/server/unknown?days=7")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 3: server time-series — filtered by axis
    r = client.get("/api/axis-score-time-series/server/ats-srv-1?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"axis filter: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for p in d["points"])

    # Test 4: server time-series — invalid axis
    r = client.get("/api/axis-score-time-series/server/ats-srv-1?days=30&axis_name=bad_axis")
    assert r.status_code == 400, f"expected 400, got {r.status_code}"

    # Test 5: distribution
    r = client.get("/api/axis-score-time-series/distribution?days=30")
    assert r.status_code == 200, f"distribution 200: {r.text}"
    d = r.json()
    assert d["total_servers"] == 2
    assert len(d["axes"]) == 7
    axis_names = {a["axis_name"] for a in d["axes"]}
    for ax in axes:
        assert ax in axis_names, f"missing {ax}"

    # Test 6: axis trend — happy path
    r = client.get("/api/axis-score-time-series/axis/overall_risk?days=30")
    assert r.status_code == 200, f"axis trend 200: {r.text}"
    d = r.json()
    assert d["axis_name"] == "overall_risk"
    assert len(d["points"]) >= 1

    # Test 7: axis trend — invalid axis
    r = client.get("/api/axis-score-time-series/axis/invalid_axis?days=30")
    assert r.status_code == 400, f"expected 400, got {r.status_code}"

    # Test 8: signal timeline (empty mesh — mesh unavailable returns empty list gracefully)
    r = client.get("/api/axis-score-time-series/signal-timeline?days=7")
    assert r.status_code == 200, f"signal-timeline: {r.text}"
    d = r.json()
    assert "points" in d

    print("Self-test PASSED")
    sys.exit(0)
