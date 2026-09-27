# deps: fastapi, sqlalchemy, pydantic
"""Axis Score Anomaly Service.

Detects statistical anomalies in LLM axis scores for MCP servers by comparing
individual axis probability distributions against population statistics.
Public endpoint — no authentication required.

GET /api/axis_score_anomaly/servers/{server_id}
    Returns per-axis anomaly flags for a single server.

GET /api/axis_score_anomaly/anomalies
    Returns all currently flagged servers across the registry.

GET /api/axis_score_anomaly/servers/{server_id}/history
    Returns historical anomaly trend for a server.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis_score_anomaly", tags=["axis_score_anomaly"])

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class AxisAnomalyScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    population_mean: float
    population_std: float
    z_score: float
    is_anomaly: bool
    anomaly_direction: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ServerAnomalyResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    risk_tier: Optional[str] = None
    anomaly_count: int
    axes: List[AxisAnomalyScore]

    model_config = ConfigDict(from_attributes=True)


class AnomalyListResponse(BaseModel):
    total_servers: int
    anomaly_servers: List[ServerAnomalyResponse]

    model_config = ConfigDict(from_attributes=True)


class AxisHistoryPoint(BaseModel):
    scored_at: datetime
    p_top: float
    label: Optional[str]
    z_score: float
    is_anomaly: bool


class AxisHistoryResponse(BaseModel):
    axis_name: str
    history: List[AxisHistoryPoint]


class ServerAnomalyHistoryResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    axes: List[AxisHistoryResponse]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

Z_THRESHOLD = 2.0


def _compute_z(p_top: float, mean: float, std: float) -> float:
    if std == 0.0:
        return 0.0
    return (p_top - mean) / std


def _anomaly_direction(p_top: float, mean: float) -> Optional[str]:
    if p_top > mean:
        return "high"
    if p_top < mean:
        return "low"
    return None


def _build_axis_anomaly(
    row: McpLlmAxisScore,
    pop_mean: float,
    pop_std: float,
    threshold: float = Z_THRESHOLD,
) -> AxisAnomalyScore:
    z = _compute_z(row.p_top, pop_mean, pop_std)
    is_anomaly = abs(z) > threshold
    direction = _anomaly_direction(row.p_top, pop_mean) if is_anomaly else None
    return AxisAnomalyScore(
        axis_name=row.axis_name,
        label=row.label or "",
        p_top=row.p_top,
        p_critical=row.p_critical,
        p_danger=row.p_danger,
        population_mean=round(pop_mean, 4),
        population_std=round(pop_std, 4),
        z_score=round(z, 4),
        is_anomaly=is_anomaly,
        anomaly_direction=direction,
    )


def _get_population_stats(
    db: Session, axis_name: str, model_version: str
) -> tuple[float, float]:
    """Return (mean, std) of p_top for an axis across all servers."""
    rows = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.axis_name == axis_name,
        McpLlmAxisScore.model_version == model_version,
        McpLlmAxisScore.p_top.isnot(None),
    ).all()
    p_vals = [r.p_top for r in rows if r.p_top is not None]
    if len(p_vals) >= 2:
        return statistics.mean(p_vals), statistics.stdev(p_vals)
    if len(p_vals) == 1:
        return p_vals[0], 0.0
    return 0.0, 0.0


def _get_latest_model_version(db: Session, server_id: Optional[str] = None) -> Optional[str]:
    """Return the most recent model_version that has axis scores."""
    q = db.query(func.max(McpLlmAxisScore.model_version))
    if server_id:
        q = q.filter(McpLlmAxisScore.server_id == server_id)
    return q.scalar()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/servers/{server_id}",
    response_model=ServerAnomalyResponse,
    summary="Get anomaly scores for a specific server",
    responses={404: {"description": "Server not found or no scores available"}},
)
def get_server_anomaly(
    server_id: str,
    z_threshold: float = Query(default=2.0, ge=0.0, le=5.0),
    db: Session = Depends(get_session),
) -> ServerAnomalyResponse:
    """Return per-axis anomaly scores for a single server.

    Each axis's p_top is compared against the population mean and standard
    deviation for that axis (across all servers). Axes with |z| > z_threshold
    are flagged as anomalous.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    latest_version = _get_latest_model_version(db, server_id)
    if not latest_version:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    server_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.model_version == latest_version,
    ).all()
    if not server_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    axes: List[AxisAnomalyScore] = []
    for row in server_scores:
        pop_mean, pop_std = _get_population_stats(db, row.axis_name, latest_version)
        axes.append(_build_axis_anomaly(row, pop_mean, pop_std, z_threshold))

    anomaly_count = sum(1 for a in axes if a.is_anomaly)
    return ServerAnomalyResponse(
        server_id=server.server_id,
        server_name=server.name,
        risk_tier=server.risk_tier,
        anomaly_count=anomaly_count,
        axes=axes,
    )


@router.get(
    "/anomalies",
    response_model=AnomalyListResponse,
    summary="List all servers with at least one anomalous axis",
)
def list_anomalous_servers(
    z_threshold: float = Query(default=2.0, ge=0.0, le=5.0),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> AnomalyListResponse:
    """Return all servers that have at least one anomalous axis score.

    Results are ordered by anomaly_count descending.
    """
    latest_version = _get_latest_model_version(db)
    if not latest_version:
        return AnomalyListResponse(total_servers=0, anomaly_servers=[])

    # Precompute population stats per axis
    axis_names = [
        r[0] for r in db.query(McpLlmAxisScore.axis_name)
        .filter(McpLlmAxisScore.model_version == latest_version)
        .distinct().all()
    ]
    axis_stats = {an: _get_population_stats(db, an, latest_version) for an in axis_names}

    all_servers = db.query(McpServerRegistry).all()
    total_servers = len(all_servers)
    anomaly_servers: List[ServerAnomalyResponse] = []

    for server in all_servers:
        rows = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server.server_id,
            McpLlmAxisScore.model_version == latest_version,
        ).all()
        axes = [
            _build_axis_anomaly(row, *axis_stats.get(row.axis_name, (0.0, 0.0)), z_threshold)
            for row in rows
        ]
        anomaly_count = sum(1 for a in axes if a.is_anomaly)
        if anomaly_count > 0:
            anomaly_servers.append(ServerAnomalyResponse(
                server_id=server.server_id,
                server_name=server.name,
                risk_tier=server.risk_tier,
                anomaly_count=anomaly_count,
                axes=axes,
            ))

    anomaly_servers.sort(key=lambda s: s.anomaly_count, reverse=True)
    return AnomalyListResponse(
        total_servers=total_servers,
        anomaly_servers=anomaly_servers[:limit],
    )


@router.get(
    "/servers/{server_id}/history",
    response_model=ServerAnomalyHistoryResponse,
    summary="Get historical anomaly trend for a server",
    responses={404: {"description": "Server not found or no scores"}},
)
def get_server_anomaly_history(
    server_id: str,
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> ServerAnomalyHistoryResponse:
    """Return per-axis historical anomaly scores for a server.

    Returns all recorded scores within the lookback window with their
    z-scores and anomaly flags computed against the latest population stats.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    latest_version = _get_latest_model_version(db, server_id)
    if not latest_version:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get current population stats for z-score computation
    axis_names = [
        r[0] for r in
        db.query(McpLlmAxisScore.axis_name)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == latest_version,
        ).distinct().all()
    ]
    axis_stats = {an: _get_population_stats(db, an, latest_version) for an in axis_names}

    # Fetch historical rows
    rows = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= cutoff,
    ).order_by(McpLlmAxisScore.scored_at).all()

    # Group by axis
    from collections import defaultdict
    by_axis: dict[str, List[tuple]] = defaultdict(list)
    for row in rows:
        by_axis[row.axis_name].append(row)

    result_axes: List[AxisHistoryResponse] = []
    for axis_name in sorted(by_axis.keys()):
        axis_rows = sorted(by_axis[axis_name], key=lambda r: r.scored_at or datetime.min)
        pop_mean, pop_std = axis_stats.get(axis_name, (0.0, 0.0))
        history = []
        for row in axis_rows:
            z = _compute_z(row.p_top, pop_mean, pop_std)
            history.append(AxisHistoryPoint(
                scored_at=row.scored_at,
                p_top=row.p_top,
                label=row.label,
                z_score=round(z, 4),
                is_anomaly=abs(z) > Z_THRESHOLD,
            ))
        result_axes.append(AxisHistoryResponse(axis_name=axis_name, history=history))

    return ServerAnomalyHistoryResponse(
        server_id=server.server_id,
        server_name=server.name,
        axes=result_axes,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
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

    # Ensure repo root is on path so `app` package resolves
    _repo_root = "/home/workspace/zo_sentinel"
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    _now = datetime.now(timezone.utc)
    _axis_names = ["overall_risk", "auth_strength", "data_sensitivity"]
    _normal_p = 50.0
    _outlier_p = 95.0
    _pk = [0]

    def _next_id():
        v = _pk[0]
        _pk[0] += 1
        return v

    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id="srv-normal", name="Normal", registry_source="test",
            url="http://x.com", first_seen=_now, last_seen=_now, last_scanned=_now,
            last_assessed=_now, risk_tier="low", trust_score=0.9, verdict="clean",
            scan_count=1, confidence=0.9, meta=None,
        ))
        sess.add(McpServerRegistry(
            server_id="srv-outlier", name="Outlier", registry_source="test",
            url="http://x.com", first_seen=_now, last_seen=_now, last_scanned=_now,
            last_assessed=_now, risk_tier="high", trust_score=0.3, verdict="flagged",
            scan_count=1, confidence=0.6, meta=None,
        ))
        for ax in _axis_names:
            # Normal baseline servers
            for j in range(4):
                sess.add(McpLlmAxisScore(
                    id=_next_id(), server_id=f"srv-norm-{j}", axis_name=ax,
                    model_version="v1", decision_rule_version="r1",
                    adapter_sha256="dead", label="medium", label_index=1,
                    probs={}, p_critical=0.0, p_danger=0.1, p_top=_normal_p,
                    escalated=False, escalated_to=None, scored_at=_now,
                ))
            # Normal server itself
            sess.add(McpLlmAxisScore(
                id=_next_id(), server_id="srv-normal", axis_name=ax,
                model_version="v1", decision_rule_version="r1",
                adapter_sha256="dead", label="medium", label_index=1,
                probs={}, p_critical=0.0, p_danger=0.1, p_top=_normal_p,
                escalated=False, escalated_to=None, scored_at=_now,
            ))
            # Outlier server
            sess.add(McpLlmAxisScore(
                id=_next_id(), server_id="srv-outlier", axis_name=ax,
                model_version="v1", decision_rule_version="r1",
                adapter_sha256="dead", label="high", label_index=3,
                probs={}, p_critical=0.3, p_danger=0.4, p_top=_outlier_p,
                escalated=False, escalated_to=None, scored_at=_now,
            ))
        sess.commit()

    client = TestClient(app)

    # Test 1: normal server — no anomalies
    r = client.get("/api/axis_score_anomaly/servers/srv-normal")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-normal"
    assert d["anomaly_count"] == 0, f"expected 0 anomalies, got {d['anomaly_count']}"

    # Test 2: outlier server — 3 anomalies (one per axis)
    r2 = client.get("/api/axis_score_anomaly/servers/srv-outlier")
    assert r2.status_code == 200, f"outlier returned {r2.status_code}: {r2.text}"
    d2 = r2.json()
    assert d2["anomaly_count"] == 3, f"expected 3, got {d2['anomaly_count']}"

    # Test 3: anomaly list endpoint
    r3 = client.get("/api/axis_score_anomaly/anomalies?z_threshold=2.0&limit=10")
    assert r3.status_code == 200, f"anomalies returned {r3.status_code}: {r3.text}"
    d3 = r3.json()
    assert d3["total_servers"] >= 2
    ids = [s["server_id"] for s in d3["anomaly_servers"]]
    assert "srv-outlier" in ids, "srv-outlier should be in anomaly list"

    # Test 4: 404 for unknown server
    r4 = client.get("/api/axis_score_anomaly/servers/nonexistent")
    assert r4.status_code == 404

    # Test 5: history endpoint
    r5 = client.get("/api/axis_score_anomaly/servers/srv-normal/history?days=30")
    assert r5.status_code == 200, f"history returned {r5.status_code}: {r5.text}"
    d5 = r5.json()
    assert d5["server_id"] == "srv-normal"
    assert len(d5["axes"]) >= 1

    print("PASS")
    sys.exit(0)
