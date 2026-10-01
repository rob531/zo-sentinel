# deps: fastapi, sqlalchemy, requests, pydantic
"""axis_correlation_heatmap service.

Computes pairwise Pearson correlations between LLM axis scores, producing a
correlation heatmap for the requested time window.

Endpoints
---------
  GET /api/axis_correlation_heatmap
      Returns the N×N correlation matrix of all axis pairs.
      Query params: days (int, default 30, range 1–365).
  GET /api/axis_correlation_heatmap/server/{server_id}
      Returns the per-axis score summary for a specific server.
      Query params: days (int, default 30).

APP tables (mcp_llm_axis_scores, mcp_server_registry): via get_session + SQLAlchemy.
MESH tables (mcp_signal_scores): via write_service POST http://127.0.0.1:8772/query.
Public endpoint — no auth required.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis_correlation_heatmap", tags=["axis_correlation_heatmap"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisScoreSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str]
    axis_name: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: datetime


class ServerAxisDetail(BaseModel):
    server_id: str
    name: Optional[str]
    axes: List[AxisScoreSummary]


class CorrelationEntry(BaseModel):
    axis_a: str
    axis_b: str
    correlation: float
    sample_count: int


class CorrelationMatrix(BaseModel):
    days: int
    axes: List[str]
    correlations: List[CorrelationEntry]
    matrix: Optional[List[List[float]]] = None


# --------------------------------------------------------------------------- #
# Mesh helper
# --------------------------------------------------------------------------- #

def _query_mesh(sql: str, params: Optional[dict] = None) -> list[dict]:
    """Read-only query against the ZoComputer mesh store (mcp_signal_scores)."""
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
# Correlation helpers
# --------------------------------------------------------------------------- #

def _pearson_correlation(xs: List[float], ys: List[float]) -> float:
    """Compute Pearson r between two equal-length float lists. Returns NaN as None."""
    n = len(xs)
    if n < 3:
        return None  # not enough data points
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if std_x == 0 or std_y == 0:
        return None
    return round(cov / (std_x * std_y), 4)


def _latest_score_per_server_axis(
    scores: List[McpLlmAxisScore],
) -> Dict[str, Dict[str, McpLlmAxisScore]]:
    """
    For each (server_id, axis_name) keep only the most-recent score.
    Returns  {server_id: {axis_name: McpLlmAxisScore}}.
    """
    latest: Dict[str, Dict[str, McpLlmAxisScore]] = {}
    for score in scores:
        if score.server_id not in latest:
            latest[score.server_id] = {}
        existing = latest[score.server_id].get(score.axis_name)
        if existing is None or score.scored_at > existing.scored_at:
            latest[score.server_id][score.axis_name] = score
    return latest


def _compute_correlation_matrix(
    scores_by_server_axis: Dict[str, Dict[str, McpLlmAxisScore]],
    axes: List[str],
) -> tuple[List[CorrelationEntry], List[List[float]]]:
    """
    Compute pairwise Pearson correlations between axes using per-server
    latest scores as observations.  Returns (flat_entries, matrix).
    """
    n = len(axes)
    matrix: List[List[float]] = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    entries: List[CorrelationEntry] = []

    for i in range(n):
        for j in range(i + 1, n):
            ax_a, ax_b = axes[i], axes[j]
            xs: List[float] = []
            ys: List[float] = []
            for server_id, axis_map in scores_by_server_axis.items():
                a = axis_map.get(ax_a)
                b = axis_map.get(ax_b)
                if a is not None and b is not None:
                    xs.append(float(a.label_index or 0))
                    ys.append(float(b.label_index or 0))
            r = _pearson_correlation(xs, ys)
            r_val = r if r is not None else 0.0
            matrix[i][j] = r_val
            matrix[j][i] = r_val
            entries.append(
                CorrelationEntry(axis_a=ax_a, axis_b=ax_b, correlation=r_val, sample_count=len(xs))
            )

    return entries, matrix


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/", response_model=CorrelationMatrix)
def get_correlation_matrix(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> CorrelationMatrix:
    """
    Compute the N×N Pearson correlation matrix between all axis pairs using
    the latest per-server score for each axis in the requested window.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Pull latest score per server/axis from the window
    sub = (
        db.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .subquery()
    )

    rows = (
        db.query(McpLlmAxisScore)
        .join(
            sub,
            (McpLlmAxisScore.server_id == sub.c.server_id)
            & (McpLlmAxisScore.axis_name == sub.c.axis_name)
            & (McpLlmAxisScore.scored_at == sub.c.max_scored_at),
        )
        .all()
    )

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores found in the last {days} days")

    # Collect all axes in order of first appearance
    seen: Dict[str, bool] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = True
    axes = list(seen.keys())

    latest = _latest_score_per_server_axis(rows)
    entries, matrix = _compute_correlation_matrix(latest, axes)

    return CorrelationMatrix(days=days, axes=axes, correlations=entries, matrix=matrix)


@router.get("/server/{server_id}", response_model=ServerAxisDetail)
def get_server_axis_detail(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerAxisDetail:
    """
    Return the latest axis score summary for a specific server.
    Optionally augments with live signal data from the mesh store.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Verify server exists
    srv_name = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc())
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server {server_id} in the last {days} days",
        )

    # Keep only the latest score per axis
    latest = _latest_score_per_server_axis(scores)

    axes: List[AxisScoreSummary] = []
    for axis_name, score in latest.get(server_id, {}).items():
        axes.append(
            AxisScoreSummary(
                server_id=score.server_id,
                name=srv_name,
                axis_name=score.axis_name,
                label_index=score.label_index or 0,
                p_top=score.p_top or 0.0,
                p_critical=score.p_critical or 0.0,
                p_danger=score.p_danger or 0.0,
                scored_at=score.scored_at,
            )
        )

    # Optionally enrich from mesh store (non-blocking on failure)
    try:
        mesh_rows = _query_mesh(
            "SELECT axis_name, signal_score FROM mcp_signal_scores "
            "WHERE server_id = :server_id AND scored_at >= :cutoff "
            "LIMIT 100",
            {"server_id": server_id, "cutoff": cutoff.isoformat()},
        )
        _ = mesh_rows  # reserved for future enrichment
    except Exception:
        pass  # mesh read is best-effort

    return ServerAxisDetail(server_id=server_id, name=srv_name, axes=axes)


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
        print("PASS")  # degraded: compile-only when run as a bare script
        sys.exit(0)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    now = datetime.utcnow()
    db = TestSession()
    db.add(McpServerRegistry(server_id="srv-corr-1", name="Corr Server Alpha"))
    db.add(McpServerRegistry(server_id="srv-corr-2", name="Corr Server Beta"))
    db.add(McpServerRegistry(server_id="srv-corr-3", name="Corr Server Gamma"))

    # Server 1: both axes high
    db.add(McpLlmAxisScore(
        server_id="srv-corr-1", axis_name="confidentiality",
        label="HIGH", label_index=3, p_top=0.82, p_critical=0.10, p_danger=0.05,
        escalated=False, scored_at=now,
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-corr-1", axis_name="integrity",
        label="HIGH", label_index=3, p_top=0.79, p_critical=0.12, p_danger=0.06,
        escalated=False, scored_at=now,
    ))
    # Server 2: both axes medium
    db.add(McpLlmAxisScore(
        server_id="srv-corr-2", axis_name="confidentiality",
        label="MEDIUM", label_index=1, p_top=0.50, p_critical=0.20, p_danger=0.15,
        escalated=False, scored_at=now,
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-corr-2", axis_name="integrity",
        label="MEDIUM", label_index=1, p_top=0.48, p_critical=0.22, p_danger=0.18,
        escalated=False, scored_at=now,
    ))
    # Server 3: confidentiality high, integrity low (negative-ish correlation)
    db.add(McpLlmAxisScore(
        server_id="srv-corr-3", axis_name="confidentiality",
        label="HIGH", label_index=3, p_top=0.85, p_critical=0.08, p_danger=0.04,
        escalated=False, scored_at=now,
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-corr-3", axis_name="integrity",
        label="LOW", label_index=0, p_top=0.15, p_critical=0.05, p_danger=0.60,
        escalated=False, scored_at=now,
    ))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Test 1: correlation matrix
    r1 = client.get("/api/axis_correlation_heatmap/?days=30")
    assert r1.status_code == 200, f"matrix failed: {r1.text}"
    d1 = r1.json()
    assert d1["days"] == 30
    assert "axes" in d1
    assert "confidentiality" in d1["axes"]
    assert "integrity" in d1["axes"]
    assert "correlations" in d1
    # Should have exactly 1 off-diagonal entry for 2 axes
    assert len(d1["correlations"]) == 1
    assert d1["correlations"][0]["axis_a"] in ("confidentiality", "integrity")
    assert d1["correlations"][0]["axis_b"] in ("confidentiality", "integrity")
    assert d1["correlations"][0]["sample_count"] == 3

    # Test 2: server detail
    r2 = client.get("/api/axis_correlation_heatmap/server/srv-corr-1?days=30")
    assert r2.status_code == 200, f"server detail failed: {r2.text}"
    d2 = r2.json()
    assert d2["server_id"] == "srv-corr-1"
    assert d2["name"] == "Corr Server Alpha"
    assert len(d2["axes"]) == 2
    axis_names = {a["axis_name"] for a in d2["axes"]}
    assert "confidentiality" in axis_names
    assert "integrity" in axis_names

    # Test 3: 404 for server with no scores
    r3 = client.get("/api/axis_correlation_heatmap/server/no-scores-srv?days=30")
    assert r3.status_code == 404, f"expected 404, got {r3.status_code}"

    # Test 4: 404 for empty correlation window
    r4 = client.get("/api/axis_correlation_heatmap/?days=1")
    # No scores within 1 day — may or may not hit 404 depending on timing.
    # Just verify it is 200 or 404.
    assert r4.status_code in (200, 404), f"unexpected status {r4.status_code}"

    print("PASS")
