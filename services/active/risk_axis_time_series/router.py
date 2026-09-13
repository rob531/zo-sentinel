# deps: fastapi, pydantic, sqlalchemy, requests
"""risk_axis_time_series -- time-series view of LLM risk-axis scores.

Endpoints
  GET /api/risk-axis/overview               -- aggregate risk-axis stats across all servers
  GET /api/risk-axis/axis/{axis_name}/trend -- time-series for a specific axis
  GET /api/risk-axis/server/{server_id}     -- per-server axis score history
  GET /api/risk-axis/tier-distribution     -- risk-tier proportion over time

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint (auth=public).
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

router = APIRouter(prefix="/api/risk-axis", tags=["risk_axis_time_series"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisStat(BaseModel):
    axis_name: str
    count: int
    avg_p_top: float | None
    avg_p_critical: float | None
    avg_p_danger: float | None
    escalated_count: int


class OverviewResponse(BaseModel):
    as_of: str
    total_servers: int
    axes: List[AxisStat]


class TrendPoint(BaseModel):
    day: str
    axis_name: str
    avg_p_top: float | None
    avg_p_critical: float | None
    avg_p_danger: float | None
    count: int
    escalated_count: int


class AxisTrendResponse(BaseModel):
    axis_name: str
    days: int
    points: List[TrendPoint]


class ServerAxisPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: Optional[str]
    scored_at: datetime


class ServerAxisHistoryResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    days: int
    points: List[ServerAxisPoint]


class TierDistributionPoint(BaseModel):
    day: str
    critical: int
    high: int
    medium: int
    low: int
    minimal: int


class TierDistributionResponse(BaseModel):
    days: int
    points: List[TierDistributionPoint]


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
# Helpers
# --------------------------------------------------------------------------- #

def _derive_tier(p_top: float, p_critical: float = 0.0, p_danger: float = 0.0) -> str:
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

@router.get("/overview", response_model=OverviewResponse)
def get_risk_overview(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> OverviewResponse:
    """
    Aggregate risk-axis stats across all servers for the lookback window.
    Uses only the overall_risk axis for server-level stats.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    total_servers: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    stats = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count().label("cnt"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.sum(func.cast(McpLlmAxisScore.escalated, Integer)).label("esc_cnt"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    axes = [
        AxisStat(
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


@router.get("/axis/{axis_name}/trend", response_model=AxisTrendResponse)
def get_axis_trend(
    axis_name: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisTrendResponse:
    """
    Time-series of axis score distributions grouped by day.
    Uses daily aggregation.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = db.execute(
        select(
            func.date(McpLlmAxisScore.scored_at).label("day"),
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.count().label("cnt"),
            func.sum(func.cast(McpLlmAxisScore.escalated, Integer)).label("esc_cnt"),
        )
        .where(McpLlmAxisScore.axis_name == axis_name)
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(func.date(McpLlmAxisScore.scored_at), McpLlmAxisScore.axis_name)
        .order_by(func.date(McpLlmAxisScore.scored_at))
    ).all()

    points = [
        TrendPoint(
            day=str(row.day),
            axis_name=row.axis_name,
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else None,
            avg_p_critical=round(float(row.avg_p_critical), 4) if row.avg_p_critical is not None else None,
            avg_p_danger=round(float(row.avg_p_danger), 4) if row.avg_p_danger is not None else None,
            count=row.cnt or 0,
            escalated_count=row.esc_cnt or 0,
        )
        for row in rows
    ]

    return AxisTrendResponse(axis_name=axis_name, days=days, points=points)


@router.get("/server/{server_id}", response_model=ServerAxisHistoryResponse)
def get_server_axis_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> ServerAxisHistoryResponse:
    """
    Per-server axis score history over the lookback window.
    Optionally filter to a single axis_name.
    """
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = query.order_by(McpLlmAxisScore.scored_at.desc()).all()

    points = [
        ServerAxisPoint(
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

    return ServerAxisHistoryResponse(
        server_id=server_id,
        server_name=srv,
        days=days,
        points=points,
    )


@router.get("/tier-distribution", response_model=TierDistributionResponse)
def get_tier_distribution(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TierDistributionResponse:
    """
    Daily risk-tier proportion derived from overall_risk axis scores.
    """
    from sqlalchemy import Integer

    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = db.execute(
        select(
            func.date(McpLlmAxisScore.scored_at).label("day"),
            func.count(
                func.case(
                    (McpLlmAxisScore.p_top >= 0.8, 1),
                    else_=None,
                )
            ).label("critical"),
            func.count(
                func.case(
                    ((McpLlmAxisScore.p_top >= 0.6, 1), (McpLlmAxisScore.p_danger >= 0.7, 1)),
                    else_=None,
                )
            ).label("high"),
            func.count(
                func.case(
                    ((McpLlmAxisScore.p_top >= 0.4, 1), (McpLlmAxisScore.p_danger >= 0.5, 1)),
                    else_=None,
                )
            ).label("medium"),
            func.count(
                func.case(
                    ((McpLlmAxisScore.p_top >= 0.2, 1), (McpLlmAxisScore.p_danger >= 0.3, 1)),
                    else_=None,
                )
            ).label("low"),
            func.count(
                func.case(
                    (
                        (McpLlmAxisScore.p_top < 0.2, 1),
                        (McpLlmAxisScore.p_danger < 0.3, 1),
                    ),
                    else_=None,
                )
            ).label("minimal"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(func.date(McpLlmAxisScore.scored_at))
        .order_by(func.date(McpLlmAxisScore.scored_at))
    ).all()

    points = [
        TierDistributionPoint(
            day=str(row.day),
            critical=row.critical or 0,
            high=row.high or 0,
            medium=row.medium or 0,
            low=row.low or 0,
            minimal=row.minimal or 0,
        )
        for row in rows
    ]

    return TierDistributionResponse(days=days, points=points)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, Integer
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

    axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]
    # Server 1: two scoring rounds
    for day_delta, p_top, label in [(5, 0.35, "LOW"), (0, 0.72, "HIGH")]:
        for ax in axes:
            db.add(McpLlmAxisScore(
                server_id="ts-srv-1", axis_name=ax, label=label, label_index=0,
                p_top=p_top, p_critical=0.1, p_danger=0.2, escalated=False,
                model_version="v1", scored_at=now - timedelta(days=day_delta),
                adapter_sha256="sha256test", decision_rule_version="v1",
                escalated_to=None, probs=None, id=None,
            ))
    # Server 2: one scoring round
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
    r = client.get("/api/risk-axis/overview?days=30")
    assert r.status_code == 200, f"overview 200: {r.text}"
    d = r.json()
    assert d["total_servers"] == 2, f"expected 2 servers, got {d['total_servers']}"
    assert len(d["axes"]) == 7, f"expected 7 axes, got {len(d['axes'])}"
    axis_names = {a["axis_name"] for a in d["axes"]}
    for ax in axes:
        assert ax in axis_names, f"missing axis {ax}"

    # Test 2: axis trend
    r = client.get("/api/risk-axis/axis/overall_risk/trend?days=30")
    assert r.status_code == 200, f"axis trend 200: {r.text}"
    d = r.json()
    assert d["axis_name"] == "overall_risk"
    assert len(d["points"]) >= 1, "expected at least 1 trend point"

    # Test 3: server axis history
    r = client.get("/api/risk-axis/server/ts-srv-1?days=30")
    assert r.status_code == 200, f"server history 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "ts-srv-1"
    assert d["server_name"] == "TS Server 1"
    assert len(d["points"]) == 14, f"expected 14 points (7 axes x 2 rounds), got {len(d['points'])}"

    # Test 4: server history filtered by axis
    r = client.get("/api/risk-axis/server/ts-srv-1?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"filtered history: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for p in d["points"])

    # Test 5: tier distribution
    r = client.get("/api/risk-axis/tier-distribution?days=30")
    assert r.status_code == 200, f"tier-distribution 200: {r.text}"
    d = r.json()
    assert len(d["points"]) >= 1, "expected at least 1 tier-distribution point"
    for p in d["points"]:
        assert set(p.keys()) == {"day", "critical", "high", "medium", "low", "minimal"}

    print("Self-test PASSED")
    sys.exit(0)
