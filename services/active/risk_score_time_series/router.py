# deps: fastapi, pydantic, sqlalchemy, requests
"""risk_score_time_series -- time-series of risk scores across MCP servers.

Endpoints
  GET /api/risk-score/overview          -- aggregate risk stats across all servers
  GET /api/risk-score/server/{server_id} -- per-server risk score history
  GET /api/risk-score/trend             -- daily risk score trend over N days

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint (auth=public).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, case
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_score_time_series"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class OverviewStat(BaseModel):
    axis_name: str
    count: int
    avg_p_top: float | None
    avg_p_critical: float | None
    avg_p_danger: float | None
    escalated_count: int


class OverviewResponse(BaseModel):
    as_of: str
    total_servers: int
    axes: List[OverviewStat]


class TrendPoint(BaseModel):
    day: str
    avg_p_top: float | None
    avg_p_critical: float | None
    avg_p_danger: float | None
    count: int
    escalated_count: int


class TrendResponse(BaseModel):
    days: int
    points: List[TrendPoint]


class ServerHistoryPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: Optional[str]
    scored_at: datetime


class ServerHistoryResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    days: int
    points: List[ServerHistoryPoint]


# --------------------------------------------------------------------------- #
# Mesh helpers
# --------------------------------------------------------------------------- #

def _query_mesh(sql: str, params: Optional[dict] = None) -> List[dict]:
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

@router.get("/risk-score/overview", response_model=OverviewResponse)
def get_risk_overview(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> OverviewResponse:
    """Aggregate risk-axis stats across all servers for the lookback window."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    total_servers: int = (
        db.execute(select(func.count(McpServerRegistry.server_id))).scalar_one() or 0
    )

    stats = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count().label("cnt"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.sum(func.cast(McpLlmAxisScore.escalated, int)).label("esc_cnt"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    axes = [
        OverviewStat(
            axis_name=row.axis_name,
            count=row.cnt,
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else None,
            avg_p_critical=round(float(row.avg_p_critical), 4) if row.avg_p_critical is not None else None,
            avg_p_danger=round(float(row.avg_p_danger), 4) if row.avg_p_danger is not None else None,
            escalated_count=row.esc_cnt or 0,
        )
        for row in stats
    ]

    return OverviewResponse(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_servers=total_servers,
        axes=axes,
    )


@router.get("/risk-score/trend", response_model=TrendResponse)
def get_risk_trend(
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> TrendResponse:
    """Daily aggregated risk score trend over the lookback window."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = select(
        func.date(McpLlmAxisScore.scored_at).label("day"),
        func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
        func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
        func.count().label("cnt"),
        func.sum(func.cast(McpLlmAxisScore.escalated, int)).label("esc_cnt"),
    ).where(McpLlmAxisScore.scored_at >= cutoff)

    if axis_name:
        query = query.where(McpLlmAxisScore.axis_name == axis_name)

    rows = (
        query.group_by(func.date(McpLlmAxisScore.scored_at))
        .order_by(func.date(McpLlmAxisScore.scored_at))
        .all()
    )

    points = [
        TrendPoint(
            day=str(row.day),
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else None,
            avg_p_critical=round(float(row.avg_p_critical), 4) if row.avg_p_critical is not None else None,
            avg_p_danger=round(float(row.avg_p_danger), 4) if row.avg_p_danger is not None else None,
            count=row.cnt or 0,
            escalated_count=row.esc_cnt or 0,
        )
        for row in rows
    ]

    return TrendResponse(days=days, points=points)


@router.get("/risk-score/server/{server_id}", response_model=ServerHistoryResponse)
def get_server_risk_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> ServerHistoryResponse:
    """Per-server risk score history over the lookback window."""
    srv_name = (
        db.execute(
            select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
        ).scalar_one_or_none()
    )

    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= cutoff,
    )
    if axis_name:
        q = q.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = q.order_by(McpLlmAxisScore.scored_at.desc()).all()

    points = [
        ServerHistoryPoint(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            model_version=r.model_version,
            scored_at=r.scored_at,
        )
        for r in rows
    ]

    return ServerHistoryResponse(
        server_id=server_id,
        server_name=srv_name,
        days=days,
        points=points,
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
        server_id="ts-srv-1", name="TS Server 1", registry_source="test",
        url="http://ts1", description="test", trust_score=0.8, verdict="clean",
        confidence=0.9, last_assessed=now, first_seen=now, last_seen=now,
        last_scanned=now, scan_count=1, risk_tier="medium", meta={},
    ))
    db.add(McpServerRegistry(
        server_id="ts-srv-2", name="TS Server 2", registry_source="test",
        url="http://ts2", description="test", trust_score=0.9, verdict="clean",
        confidence=1.0, last_assessed=now, first_seen=now, last_seen=now,
        last_scanned=now, scan_count=1, risk_tier="low", meta={},
    ))

    axes = ["overall_risk", "auth_strength", "capability_breadth"]
    for day_delta, p_top, label in [(5, 0.35, "LOW"), (0, 0.72, "HIGH")]:
        for ax in axes:
            db.add(McpLlmAxisScore(
                server_id="ts-srv-1", axis_name=ax, label=label, label_index=0,
                p_top=p_top, p_critical=0.1, p_danger=0.2, escalated=False,
                model_version="v1", scored_at=now - timedelta(days=day_delta),
                adapter_sha256="sha256test", decision_rule_version="v1",
                escalated_to=None, probs=None, id=None,
            ))
    for ax in axes:
        db.add(McpLlmAxisScore(
            server_id="ts-srv-2", axis_name=ax, label="LOW", label_index=0,
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

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    # Test 1: overview
    r = client.get("/api/risk-score/overview?days=30")
    assert r.status_code == 200, f"overview 200: {r.text}"
    d = r.json()
    assert d["total_servers"] == 2, f"expected 2 servers, got {d['total_servers']}"
    assert len(d["axes"]) == 3, f"expected 3 axes, got {len(d['axes'])}"

    # Test 2: trend
    r = client.get("/api/risk-score/trend?days=30")
    assert r.status_code == 200, f"trend 200: {r.text}"
    d = r.json()
    assert d["days"] == 30
    assert len(d["points"]) >= 1, "expected at least 1 trend point"

    # Test 3: server history
    r = client.get("/api/risk-score/server/ts-srv-1?days=30")
    assert r.status_code == 200, f"server history 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "ts-srv-1"
    assert d["server_name"] == "TS Server 1"
    assert len(d["points"]) == 6, f"expected 6 points (3 axes x 2 rounds), got {len(d['points'])}"

    # Test 4: server history filtered by axis
    r = client.get("/api/risk-score/server/ts-srv-1?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"filtered history: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for p in d["points"])

    print("Self-test PASSED")
    sys.exit(0)
