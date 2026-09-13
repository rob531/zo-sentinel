# deps: fastapi, sqlalchemy, pydantic
"""Scoring Anomaly Probe Service.

Detects statistical anomalies in LLM axis scores for MCP servers by comparing
individual axis probability distributions against population statistics.
Public endpoint — no authentication required.

GET /api/scoring_anomaly_probe/servers/{server_id}
    Returns per-axis anomaly flags for a single server.

GET /api/scoring_anomaly_probe/anomalies
    Returns all currently flagged servers across the registry.
"""
from __future__ import annotations

import sys
import statistics
from pathlib import Path
from typing import Generator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Ensure app package is importable from repo root.
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scoring_anomaly_probe", tags=["scoring_anomaly_probe"])

# ---------------------------------------------------------------------------
# Pydantic request / response models
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
    anomaly_direction: Optional[str] = None  # "high" | "low" | None

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

Z_THRESHOLD = 2.0  # Flag axes where |z| > this as anomalous.


def _compute_z(p_top: float, mean: float, std: float) -> float:
    if std == 0:
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
        label=row.label,
        p_top=row.p_top,
        p_critical=row.p_critical,
        p_danger=row.p_danger,
        population_mean=round(pop_mean, 4),
        population_std=round(pop_std, 4),
        z_score=round(z, 4),
        is_anomaly=is_anomaly,
        anomaly_direction=direction,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/servers/{server_id}",
    response_model=ServerAnomalyResponse,
    summary="Get anomaly scores for a specific server",
)
def get_server_anomaly(
    server_id: str,
    z_threshold: float = Query(default=2.0, ge=0.0, le=5.0, description="Z-score threshold for anomaly flag"),
    db: Session = Depends(get_session),
) -> ServerAnomalyResponse:
    """
    Return per-axis anomaly scores for a single server.

    Each axis's p_top is compared against the population mean and standard
    deviation for that axis (across all servers).  Axes with |z| > ``z_threshold``
    are flagged as anomalous.
    """
    # Resolve server
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Determine the latest model_version for this server
    latest_version = db.query(func.max(McpLlmAxisScore.model_version)).filter(
        McpLlmAxisScore.server_id == server_id
    ).scalar()
    if not latest_version:
        raise HTTPException(status_code=404, detail="No scores found for server")

    # Fetch this server's axis scores
    server_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.model_version == latest_version,
    ).all()

    if not server_scores:
        raise HTTPException(status_code=404, detail="No scores found for server")

    # Compute population stats per axis (all servers, latest version)
    axes: List[AxisAnomalyScore] = []
    for row in server_scores:
        pop_rows = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.axis_name == row.axis_name,
            McpLlmAxisScore.model_version == latest_version,
        ).all()
        p_vals = [r.p_top for r in pop_rows if r.p_top is not None]
        if len(p_vals) >= 2:
            pop_mean = statistics.mean(p_vals)
            pop_std = statistics.stdev(p_vals)
        elif len(p_vals) == 1:
            pop_mean = pop_std = p_vals[0]
        else:
            pop_mean = pop_std = 0.0

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
    z_threshold: float = Query(default=2.0, ge=0.0, le=5.0, description="Z-score threshold"),
    limit: int = Query(default=50, ge=1, le=500, description="Max servers to return"),
    db: Session = Depends(get_session),
) -> AnomalyListResponse:
    """
    Return all servers that have at least one anomalous axis score.

    Results are ordered by anomaly_count descending.
    """
    # Get the globally latest model_version
    latest_version = db.query(func.max(McpLlmAxisScore.model_version)).scalar()
    if not latest_version:
        return AnomalyListResponse(total_servers=0, anomaly_servers=[])

    # Compute population stats per axis
    axis_stats: dict[str, tuple[float, float]] = {}
    for axis_name, in db.query(McpLlmAxisScore.axis_name).filter(
        McpLlmAxisScore.model_version == latest_version
    ).distinct().all():
        p_vals = [
            r.p_top for r in
            db.query(McpLlmAxisScore).filter(
                McpLlmAxisScore.axis_name == axis_name,
                McpLlmAxisScore.model_version == latest_version,
            ).all()
            if r.p_top is not None
        ]
        if len(p_vals) >= 2:
            axis_stats[axis_name] = (statistics.mean(p_vals), statistics.stdev(p_vals))
        elif len(p_vals) == 1:
            axis_stats[axis_name] = (p_vals[0], 0.0)
        else:
            axis_stats[axis_name] = (0.0, 0.0)

    # Per-server aggregation
    all_servers = db.query(McpServerRegistry).all()
    total_servers = len(all_servers)
    anomaly_servers: List[ServerAnomalyResponse] = []

    for server in all_servers:
        rows = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server.server_id,
            McpLlmAxisScore.model_version == latest_version,
        ).all()
        axes: List[AxisAnomalyScore] = []
        for row in rows:
            pop_mean, pop_std = axis_stats.get(row.axis_name, (0.0, 0.0))
            axes.append(_build_axis_anomaly(row, pop_mean, pop_std, z_threshold))

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


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    _test_app = FastAPI()
    _test_app.include_router(router)

    _test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=_test_engine)
    _TestSessionLocal = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False)

    def _override_get_session() -> Generator[Session, None, None]:
        _sess = _TestSessionLocal()
        try:
            yield _sess
        finally:
            _sess.close()

    _test_app.dependency_overrides[get_session] = _override_get_session

    _now = datetime.now(timezone.utc)

    with _TestSessionLocal() as _sess:
        # Server A – normal scores (close to population mean)
        _sess.add(McpServerRegistry(
            server_id="srv-normal",
            name="Normal Server",
            registry_source="self-test",
            url="http://normal.example.com",
            first_seen=_now,
            last_seen=_now,
            last_scanned=_now,
            last_assessed=_now,
            risk_tier="low",
            trust_score=0.9,
            verdict="approved",
            scan_count=1,
            confidence=0.9,
            meta=None,
        ))
        # Server B – anomalous scores (very high p_top outliers)
        _sess.add(McpServerRegistry(
            server_id="srv-anomaly",
            name="Anomalous Server",
            registry_source="self-test",
            url="http://anomaly.example.com",
            first_seen=_now,
            last_seen=_now,
            last_scanned=_now,
            last_assessed=_now,
            risk_tier="high",
            trust_score=0.3,
            verdict="flagged",
            scan_count=1,
            confidence=0.6,
            meta=None,
        ))
        # Axis scores – population: 4 normal servers at ~50 p_top, 1 outlier at 95
        _axis_names = ["overall_risk", "auth_strength", "data_sensitivity"]
        _normal_p_top = 50.0
        _outlier_p_top = 95.0
        _pk_counter = [0]
        def _next_id():
            v = _pk_counter[0]
            _pk_counter[0] += 1
            return v
        for i, ax in enumerate(_axis_names):
            # 4 normal entries
            for j in range(4):
                _sess.add(McpLlmAxisScore(
                    id=_next_id(),
                    server_id=f"srv-normal-{j}",
                    axis_name=ax,
                    model_version="v1",
                    decision_rule_version="r1",
                    adapter_sha256="deadbeef",
                    label="medium",
                    label_index=1,
                    probs={},
                    p_critical=0.0,
                    p_danger=0.1,
                    p_top=_normal_p_top,
                    escalated=False,
                    escalated_to=None,
                    scored_at=_now,
                ))
            # One anomalous entry for srv-anomaly
            _sess.add(McpLlmAxisScore(
                id=_next_id(),
                server_id="srv-anomaly",
                axis_name=ax,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="deadbeef",
                label="high",
                label_index=3,
                probs={},
                p_critical=0.3,
                p_danger=0.4,
                p_top=_outlier_p_top,
                escalated=False,
                escalated_to=None,
                scored_at=_now,
            ))
            # Also add srv-normal itself so get_server_anomaly has data
            _sess.add(McpLlmAxisScore(
                id=_next_id(),
                server_id="srv-normal",
                axis_name=ax,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="deadbeef",
                label="medium",
                label_index=1,
                probs={},
                p_critical=0.0,
                p_danger=0.1,
                p_top=_normal_p_top,
                escalated=False,
                escalated_to=None,
                scored_at=_now,
            ))
        _sess.commit()

    _client = TestClient(_test_app)

    # --- Happy path: get_server_anomaly ---
    _resp = _client.get("/api/scoring_anomaly_probe/servers/srv-normal")
    if _resp.status_code != 200:
        print(f"FAIL: expected 200, got {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    if _data["server_id"] != "srv-normal":
        print(f"FAIL: wrong server_id: {_data['server_id']}")
        _sys.exit(1)
    # srv-normal has all normal p_top (50) so should have 0 anomalies at z=2.0
    if _data["anomaly_count"] != 0:
        print(f"FAIL: expected anomaly_count=0 for srv-normal, got {_data['anomaly_count']}")
        _sys.exit(1)

    # srv-anomaly should have 3 anomalies (one per axis)
    _resp2 = _client.get("/api/scoring_anomaly_probe/servers/srv-anomaly")
    if _resp2.status_code != 200:
        print(f"FAIL: srv-anomaly returned {_resp2.status_code}: {_resp2.text}")
        _sys.exit(1)
    _data2 = _resp2.json()
    if _data2["anomaly_count"] != 3:
        print(f"FAIL: expected anomaly_count=3 for srv-anomaly, got {_data2['anomaly_count']}")
        _sys.exit(1)

    # --- Anomaly list endpoint ---
    _resp3 = _client.get("/api/scoring_anomaly_probe/anomalies?z_threshold=2.0&limit=10")
    if _resp3.status_code != 200:
        print(f"FAIL: anomalies endpoint returned {_resp3.status_code}: {_resp3.text}")
        _sys.exit(1)
    _data3 = _resp3.json()
    if _data3["total_servers"] < 1:
        print(f"FAIL: total_servers should be >= 1, got {_data3['total_servers']}")
        _sys.exit(1)
    _srv_ids = [s["server_id"] for s in _data3["anomaly_servers"]]
    if "srv-anomaly" not in _srv_ids:
        print("FAIL: srv-anomaly not in anomaly list")
        _sys.exit(1)

    # --- 404 for unknown server ---
    _resp4 = _client.get("/api/scoring_anomaly_probe/servers/nonexistent")
    if _resp4.status_code != 404:
        print(f"FAIL: expected 404 for nonexistent server, got {_resp4.status_code}")
        _sys.exit(1)

    print("PASS")
    _sys.exit(0)
