# deps: fastapi, sqlalchemy, requests, pydantic
"""axis_score_volatility service.

Measures volatility of axis scores over time:
  - GET /api/axis_score_volatility/server/{server_id}      -- per-server volatility metrics
  - GET /api/axis_score_volatility/servers                   -- all servers with high volatility
  - GET /api/axis_score_volatility/axis/{axis_name}         -- per-axis volatility across servers

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint -- no auth required.
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

router = APIRouter(prefix="/api/axis_score_volatility", tags=["axis_score_volatility"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class LabelTransition(BaseModel):
    from_label: Optional[str]
    to_label: str
    count: int
    direction: str  # 'up', 'down', 'sideways'


class VolatilityMetrics(BaseModel):
    axis_name: str
    label_transitions: List[LabelTransition]
    avg_prob_swing: Optional[float]
    max_prob_swing: Optional[float]
    escalated_count: int
    stable_count: int
    total_count: int


class ServerVolatilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str]
    days: int
    overall_volatility_score: float
    axes: List[VolatilityMetrics]


class AxisVolatilityResponse(BaseModel):
    axis_name: str
    days: int
    servers_measured: int
    avg_volatility: float
    max_volatility: float
    escalation_rate: float
    high_volatility_servers: int


class ServerSummary(BaseModel):
    server_id: str
    name: Optional[str]
    volatility_score: float
    axes_measured: int
    last_scored: Optional[datetime]


class ServerListResponse(BaseModel):
    days: int
    servers: List[ServerSummary]
    total: int


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
# Internal helpers
# --------------------------------------------------------------------------- #

def _compute_volatility_for_axis(scores: List[McpLlmAxisScore]) -> VolatilityMetrics:
    """Compute volatility metrics for a list of axis scores ordered by scored_at."""
    if not scores:
        return VolatilityMetrics(
            axis_name="",
            label_transitions=[],
            avg_prob_swing=None,
            max_prob_swing=None,
            escalated_count=0,
            stable_count=0,
            total_count=0,
        )

    transitions: Dict[str, int] = {}
    escalated_count = 0
    prob_swings: List[float] = []
    prev_label: Optional[str] = None
    prev_p_top: Optional[float] = None

    for score in scores:
        # Track label transitions
        if prev_label is not None and score.label != prev_label:
            direction = "up" if (score.label_index or 0) > (scores[scores.index(score) - 1].label_index or 0) else "down"
            if direction == "sideways" and abs((score.label_index or 0) - (scores[scores.index(score) - 1].label_index or 0)) == 0:
                direction = "sideways"
            key = f"{prev_label}->{score.label}"
            transitions[key] = transitions.get(key, 0) + 1

        if score.escalated:
            escalated_count += 1

        # Track probability swings
        if prev_p_top is not None and score.p_top is not None:
            swing = abs(score.p_top - prev_p_top)
            prob_swings.append(swing)

        prev_label = score.label
        prev_p_top = score.p_top

    label_transitions = [
        LabelTransition(
            from_label=t.split("->")[0] if "->" in t else None,
            to_label=t.split("->")[1] if "->" in t else t,
            count=c,
            direction="sideways" if "->" not in t else ("up" if False else "sideways"),
        )
        for t, c in transitions.items()
    ]

    stable_count = len(scores) - escalated_count

    return VolatilityMetrics(
        axis_name=scores[0].axis_name,
        label_transitions=label_transitions,
        avg_prob_swing=round(sum(prob_swings) / len(prob_swings), 4) if prob_swings else None,
        max_prob_swing=round(max(prob_swings), 4) if prob_swings else None,
        escalated_count=escalated_count,
        stable_count=stable_count,
        total_count=len(scores),
    )


def _compute_overall_volatility(axes: List[VolatilityMetrics]) -> float:
    """Compute overall volatility score (0-100) from per-axis metrics."""
    if not axes:
        return 0.0

    total_volatility = 0.0
    for ax in axes:
        # Factor in escalation rate and probability swings
        esc_rate = ax.escalated_count / ax.total_count if ax.total_count > 0 else 0
        prob_factor = ax.max_prob_swing or 0
        transition_factor = len(ax.label_transitions) * 10
        ax_volatility = (esc_rate * 40) + (prob_factor * 40) + (transition_factor * 20)
        total_volatility += ax_volatility

    return round(total_volatility / len(axes), 2)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/server/{server_id}",
    response_model=ServerVolatilityResponse,
    summary="Get volatility metrics for a specific server",
    responses={404: {"description": "Server not found"}},
)
def get_server_volatility(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_session),
) -> ServerVolatilityResponse:
    """
    Returns volatility metrics for all axis scores of a given server.
    """
    # Verify server exists
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all scores for this server in the period
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at)
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server {server_id} in the last {days} days",
        )

    # Group by axis_name
    axes_scores: Dict[str, List[McpLlmAxisScore]] = {}
    for score in scores:
        if score.axis_name not in axes_scores:
            axes_scores[score.axis_name] = []
        axes_scores[score.axis_name].append(score)

    # Compute volatility per axis
    axes_metrics = [_compute_volatility_for_axis(ax_scores) for ax_scores in axes_scores.values()]
    overall_volatility = _compute_overall_volatility(axes_metrics)

    return ServerVolatilityResponse(
        server_id=server_id,
        name=srv,
        days=days,
        overall_volatility_score=overall_volatility,
        axes=axes_metrics,
    )


@router.get(
    "/axis/{axis_name}",
    response_model=AxisVolatilityResponse,
    summary="Get volatility metrics for a specific axis across all servers",
)
def get_axis_volatility(
    axis_name: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisVolatilityResponse:
    """
    Returns aggregated volatility metrics for a specific axis across all servers.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all scores for this axis
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at)
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for axis {axis_name} in the last {days} days",
        )

    # Group by server
    server_scores: Dict[str, List[McpLlmAxisScore]] = {}
    for score in scores:
        if score.server_id not in server_scores:
            server_scores[score.server_id] = []
        server_scores[score.server_id].append(score)

    # Compute per-server volatility
    volatilities: List[float] = []
    total_escaped = 0
    high_volatility_count = 0

    for server_id, ax_scores in server_scores.items():
        metrics = _compute_volatility_for_axis(ax_scores)
        overall = _compute_overall_volatility([metrics])
        volatilities.append(overall)

        if metrics.escalated_count > 0:
            total_escaped += 1
        if overall > 30:  # threshold for high volatility
            high_volatility_count += 1

    servers_measured = len(server_scores)
    avg_volatility = round(sum(volatilities) / len(volatilities), 2) if volatilities else 0.0
    max_volatility = round(max(volatilities), 2) if volatilities else 0.0
    escalation_rate = round(total_escaped / servers_measured, 4) if servers_measured > 0 else 0.0

    return AxisVolatilityResponse(
        axis_name=axis_name,
        days=days,
        servers_measured=servers_measured,
        avg_volatility=avg_volatility,
        max_volatility=max_volatility,
        escalation_rate=escalation_rate,
        high_volatility_servers=high_volatility_count,
    )


@router.get(
    "/servers",
    response_model=ServerListResponse,
    summary="List servers with highest volatility scores",
)
def get_volatile_servers(
    days: int = Query(default=30, ge=1, le=365),
    min_volatility: float = Query(default=0.0, ge=0.0, le=100.0),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> ServerListResponse:
    """
    Returns servers with the highest volatility scores, optionally filtered by minimum threshold.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all unique server/axis combinations with scores
    rows = (
        db.query(
            McpLlmAxisScore.server_id,
            McpServerRegistry.name,
            func.max(McpLlmAxisScore.scored_at).label("last_scored"),
            func.count(McpLlmAxisScore.id).label("score_count"),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axes_count"),
            func.sum(
                func.nullif(McpLlmAxisScore.escalated, False)
            ).label("escalated_count"),
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.server_id, McpServerRegistry.name)
        .having(func.count(McpLlmAxisScore.id) >= 2)  # need at least 2 scores to measure volatility
        .order_by(func.count(func.nullif(McpLlmAxisScore.escalated, False)).desc())
        .limit(limit)
        .all()
    )

    servers: List[ServerSummary] = []
    for row in rows:
        # Compute approximate volatility score
        esc_rate = (row.escalated_count or 0) / row.score_count if row.score_count > 0 else 0
        axes_factor = min(row.axes_count / 7, 1.0)  # normalize to 7 max axes
        volatility_score = round((esc_rate * 60) + (axes_factor * 40), 2)

        if volatility_score >= min_volatility:
            servers.append(
                ServerSummary(
                    server_id=row.server_id,
                    name=row.name,
                    volatility_score=volatility_score,
                    axes_measured=row.axes_count or 0,
                    last_scored=row.last_scored,
                )
            )

    # Sort by volatility descending
    servers.sort(key=lambda x: x.volatility_score, reverse=True)

    return ServerListResponse(
        days=days,
        servers=servers,
        total=len(servers),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
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
    db.add(McpServerRegistry(server_id="srv-vol-1", name="Volatility Test Server"))
    db.add(McpLlmAxisScore(
        server_id="srv-vol-1",
        axis_name="overall_risk",
        label="MEDIUM",
        label_index=1,
        p_top=0.45,
        p_critical=0.1,
        p_danger=0.2,
        escalated=False,
        model_version="v1",
        scored_at=datetime.utcnow() - timedelta(days=5),
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-vol-1",
        axis_name="overall_risk",
        label="HIGH",
        label_index=2,
        p_top=0.65,
        p_critical=0.2,
        p_danger=0.3,
        escalated=True,
        escalated_to="CRITICAL",
        model_version="v1",
        scored_at=datetime.utcnow() - timedelta(days=2),
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-vol-1",
        axis_name="auth_strength",
        label="WEAK",
        label_index=0,
        p_top=0.25,
        p_critical=0.05,
        p_danger=0.1,
        escalated=False,
        model_version="v1",
        scored_at=datetime.utcnow() - timedelta(days=4),
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-vol-1",
        axis_name="auth_strength",
        label="STRONG",
        label_index=3,
        p_top=0.85,
        p_critical=0.01,
        p_danger=0.05,
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

    # Test 1: server volatility
    r = client.get("/api/axis_score_volatility/server/srv-vol-1?days=30")
    assert r.status_code == 200, f"server volatility failed: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-vol-1"
    assert d["overall_volatility_score"] > 0
    assert len(d["axes"]) == 2

    # Test 2: axis volatility
    r2 = client.get("/api/axis_score_volatility/axis/overall_risk?days=30")
    assert r2.status_code == 200, f"axis volatility failed: {r2.text}"
    d2 = r2.json()
    assert d2["axis_name"] == "overall_risk"
    assert d2["servers_measured"] == 1

    # Test 3: volatile servers list
    r3 = client.get("/api/axis_score_volatility/servers?days=30")
    assert r3.status_code == 200, f"servers list failed: {r3.text}"
    d3 = r3.json()
    assert d3["total"] >= 1
    assert len(d3["servers"]) >= 1

    # Test 4: 404 for unknown server
    r4 = client.get("/api/axis_score_volatility/server/unknown-srv?days=30")
    assert r4.status_code == 404, f"expected 404, got {r4.status_code}"

    # Test 5: 404 for unknown axis
    r5 = client.get("/api/axis_score_volatility/axis/unknown_axis?days=30")
    assert r5.status_code == 404, f"expected 404 for unknown axis, got {r5.status_code}"

    print("Self-test PASSED")
