# deps: fastapi, sqlalchemy, pydantic
"""Axis Score Drift Report.

Detects and reports drift in LLM axis scores over time: p_top deltas and
label changes between consecutive scoring runs per server/axis.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, func, desc
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore

router = APIRouter(prefix="/api/axis-score-drift-report", tags=["axis_score_drift_report"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class DriftSample(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scored_at: datetime
    p_top: Optional[float] = None
    label: Optional[str] = None


class AxisDriftDetail(BaseModel):
    axis_name: str
    drift_delta: float = Field(description="Max |p_top change| between consecutive runs")
    drift_label: bool = Field(description="True if any consecutive labels differ")
    run_count: int
    first_p_top: Optional[float] = None
    last_p_top: Optional[float] = None
    first_label: Optional[str] = None
    last_label: Optional[str] = None
    samples: List[DriftSample]


class ServerDriftSummary(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    axes: List[AxisDriftDetail]
    max_drift_delta: float = Field(description="Max drift_delta across all axes")
    any_label_changed: bool
    total_runs: int


class DriftReportResponse(BaseModel):
    generated_at: str
    window_hours: int
    min_drift_threshold: float
    servers: List[ServerDriftSummary]


class DriftHistoryPoint(BaseModel):
    axis_name: str
    scored_at: datetime
    p_top: Optional[float] = None
    label: Optional[str] = None
    drift_delta: float = 0.0
    drift_label: bool = False


class DriftHistoryResponse(BaseModel):
    server_id: str
    axis_name: str
    window_hours: int
    points: List[DriftHistoryPoint]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _compute_axis_drift(session: Session, server_id: str, axis_name: str,
                        cutoff: datetime) -> AxisDriftDetail:
    """Compute drift for a specific server/axis within the lookback window."""
    scores = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server {server_id}, axis {axis_name}",
        )

    drift_delta = 0.0
    drift_label = False
    samples = []
    for s in scores:
        samples.append(DriftSample(scored_at=s.scored_at, p_top=s.p_top, label=s.label))

    for i in range(1, len(scores)):
        if scores[i].p_top is not None and scores[i - 1].p_top is not None:
            delta = abs(float(scores[i].p_top) - float(scores[i - 1].p_top))
            drift_delta = max(drift_delta, delta)
        if scores[i].label != scores[i - 1].label:
            drift_label = True

    return AxisDriftDetail(
        axis_name=axis_name,
        drift_delta=round(drift_delta, 6),
        drift_label=drift_label,
        run_count=len(scores),
        first_p_top=scores[0].p_top,
        last_p_top=scores[-1].p_top,
        first_label=scores[0].label,
        last_label=scores[-1].label,
        samples=samples,
    )


def _compute_server_summary(session: Session, server_id: str, cutoff: datetime) -> ServerDriftSummary:
    """Compute drift summary for all axes of a server."""
    axis_names = (
        session.query(McpLlmAxisScore.axis_name)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .distinct()
        .all()
    )
    axis_names = [r[0] for r in axis_names]

    if not axis_names:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server {server_id}",
        )

    axes: List[AxisDriftDetail] = []
    max_drift = 0.0
    any_label_changed = False

    for axis_name in axis_names:
        detail = _compute_axis_drift(session, server_id, axis_name, cutoff)
        axes.append(detail)
        max_drift = max(max_drift, detail.drift_delta)
        if detail.drift_label:
            any_label_changed = True

    # Resolve server name
    from app.models import McpServerRegistry
    server_row = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    server_name = server_row.name if server_row else None

    return ServerDriftSummary(
        server_id=server_id,
        server_name=server_name,
        axes=axes,
        max_drift_delta=round(max_drift, 6),
        any_label_changed=any_label_changed,
        total_runs=sum(ax.run_count for ax in axes),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/{server_id}",
    response_model=ServerDriftSummary,
    name="axis_score_drift_report:server_summary",
)
def get_server_drift_summary(
    server_id: str,
    window_hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 72,
    db: Session = Depends(get_session),
) -> ServerDriftSummary:
    """
    Return per-axis drift summary for a given server over the lookback window.
    Includes p_top delta between consecutive runs, label changes, and sample history.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)
    return _compute_server_summary(db, server_id, cutoff)


@router.get(
    "/{server_id}/{axis_name}",
    response_model=AxisDriftDetail,
    name="axis_score_drift_report:axis_detail",
)
def get_axis_drift_detail(
    server_id: str,
    axis_name: str,
    window_hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 72,
    db: Session = Depends(get_session),
) -> AxisDriftDetail:
    """
    Return drift detail for a specific server/axis combination within the lookback window.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)
    return _compute_axis_drift(db, server_id, axis_name, cutoff)


@router.get(
    "",
    response_model=DriftReportResponse,
    name="axis_score_drift_report:report",
)
def get_drift_report(
    window_hours: Annotated[int, Query(ge=1, le=720, description="Lookback window in hours")] = 72,
    min_drift: Annotated[float, Query(ge=0, le=1, description="Minimum drift delta to include server")] = 0.01,
    limit: Annotated[int, Query(ge=1, le=200, description="Max servers to return")] = 50,
    db: Session = Depends(get_session),
) -> DriftReportResponse:
    """
    Return drift report across all servers: servers with axis score drift above
    min_drift threshold, ordered by max drift delta descending.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)

    # Find servers with at least one axis score in the window
    server_ids = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .distinct()
        .limit(limit * 2)
        .all()
    )
    server_ids = [r[0] for r in server_ids]

    servers: List[ServerDriftSummary] = []
    for sid in server_ids:
        try:
            summary = _compute_server_summary(db, sid, cutoff)
        except HTTPException:
            continue
        if summary.max_drift_delta >= min_drift:
            servers.append(summary)

    servers.sort(key=lambda s: s.max_drift_delta, reverse=True)
    servers = servers[:limit]

    return DriftReportResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_hours=window_hours,
        min_drift_threshold=min_drift,
        servers=servers,
    )


@router.get(
    "/{server_id}/{axis_name}/history",
    response_model=DriftHistoryResponse,
    name="axis_score_drift_report:axis_history",
)
def get_axis_drift_history(
    server_id: str,
    axis_name: str,
    window_hours: Annotated[int, Query(ge=1, le=720)] = 168,
    db: Session = Depends(get_session),
) -> DriftHistoryResponse:
    """
    Return per-run drift history for a server/axis: each point includes the
    drift delta relative to the previous run.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)
    scores = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server {server_id}, axis {axis_name}",
        )

    points: List[DriftHistoryPoint] = []
    for i, s in enumerate(scores):
        delta = 0.0
        label_changed = False
        if i > 0 and scores[i - 1].p_top is not None and s.p_top is not None:
            delta = abs(float(s.p_top) - float(scores[i - 1].p_top))
        if i > 0 and s.label != scores[i - 1].label:
            label_changed = True
        points.append(DriftHistoryPoint(
            axis_name=axis_name,
            scored_at=s.scored_at,
            p_top=s.p_top,
            label=s.label,
            drift_delta=round(delta, 6),
            drift_label=label_changed,
        ))

    return DriftHistoryResponse(
        server_id=server_id,
        axis_name=axis_name,
        window_hours=window_hours,
        points=points,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    _app = FastAPI()
    _app.include_router(router)

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(_engine)
    _TestSession = sessionmaker(bind=_engine)
    _test_db = _TestSession()

    _now = datetime.utcnow()

    # Server "srv-001": axis "overall_risk" — 3 runs with p_top: 0.8 → 0.5 → 0.2 (drift_delta=0.3)
    # Server "srv-002": axis "auth_strength" — 2 runs with same label (drift_label=False)
    for i, (p_top, label) in enumerate([(0.8, "LOW"), (0.5, "MEDIUM"), (0.2, "HIGH")]):
        _test_db.add(McpLlmAxisScore(
            server_id="srv-001",
            axis_name="overall_risk",
            label=label,
            label_index=i,
            p_top=p_top,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha256:test",
            scored_at=_now - timedelta(hours=(3 - i) * 2),
        ))
    for p_top, label in [(0.9, "STRONG"), (0.7, "STRONG")]:
        _test_db.add(McpLlmAxisScore(
            server_id="srv-001",
            axis_name="auth_strength",
            label=label,
            label_index=0,
            p_top=p_top,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha256:test",
            scored_at=_now - timedelta(hours=4),
        ))
    # srv-002: no drift (same label)
    for p_top in [0.3, 0.31]:
        _test_db.add(McpLlmAxisScore(
            server_id="srv-002",
            axis_name="overall_risk",
            label="MEDIUM",
            label_index=1,
            p_top=p_top,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha256:test",
            scored_at=_now - timedelta(hours=2),
        ))

    _test_db.commit()

    def _override():
        try:
            yield _test_db
        finally:
            pass

    _app.dependency_overrides[get_session] = _override
    _client = TestClient(_app)

    # --- Happy path: server drift summary ---
    _resp = _client.get("/api/axis-score-drift-report/srv-001?window_hours=72")
    if _resp.status_code != 200:
        print(f"FAIL: status {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    if "axes" not in _data:
        print(f"FAIL: missing axes in server summary: {_data}")
        _sys.exit(1)
    if len(_data["axes"]) != 2:
        print(f"FAIL: expected 2 axes for srv-001, got {len(_data['axes'])}")
        _sys.exit(1)
    for _ax in _data["axes"]:
        if _ax["axis_name"] == "overall_risk":
            if _ax["drift_label"] is not True:
                print(f"FAIL: overall_risk drift_label should be True: {_ax}")
                _sys.exit(1)
            if abs(_ax["drift_delta"] - 0.3) > 0.001:
                print(f"FAIL: overall_risk drift_delta expected ~0.3, got {_ax['drift_delta']}")
                _sys.exit(1)
        if _ax["axis_name"] == "auth_strength":
            if _ax["drift_label"] is not False:
                print(f"FAIL: auth_strength drift_label should be False: {_ax}")
                _sys.exit(1)

    # --- Per-axis detail endpoint ---
    _resp2 = _client.get("/api/axis-score-drift-report/srv-001/overall_risk?window_hours=72")
    if _resp2.status_code != 200:
        print(f"FAIL: per-axis endpoint status {_resp2.status_code}: {_resp2.text}")
        _sys.exit(1)
    _ax2 = _resp2.json()
    if _ax2["drift_delta"] != 0.3:
        print(f"FAIL: per-axis drift_delta expected 0.3, got {_ax2['drift_delta']}")
        _sys.exit(1)

    # --- Drift history endpoint ---
    _resp3 = _client.get("/api/axis-score-drift-report/srv-001/overall_risk/history?window_hours=72")
    if _resp3.status_code != 200:
        print(f"FAIL: history endpoint status {_resp3.status_code}: {_resp3.text}")
        _sys.exit(1)
    _hist = _resp3.json()
    if len(_hist["points"]) != 3:
        print(f"FAIL: expected 3 history points, got {len(_hist['points'])}")
        _sys.exit(1)

    # --- Report endpoint: srv-001 has drift >= 0.01 ---
    _resp4 = _client.get("/api/axis-score-drift-report?window_hours=72&min_drift=0.01")
    if _resp4.status_code != 200:
        print(f"FAIL: report endpoint status {_resp4.status_code}: {_resp4.text}")
        _sys.exit(1)
    _report = _resp4.json()
    if "servers" not in _report or "generated_at" not in _report:
        print(f"FAIL: report missing top-level keys: {_report}")
        _sys.exit(1)

    # --- Validation: window_hours must be positive ---
    _resp5 = _client.get("/api/axis-score-drift-report/srv-001?window_hours=0")
    if _resp5.status_code != 422:
        print(f"FAIL: expected 422 for window_hours=0, got {_resp5.status_code}")
        _sys.exit(1)

    # --- 404 for unknown server ---
    _resp6 = _client.get("/api/axis-score-drift-report/unknown-srv?window_hours=72")
    if _resp6.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {_resp6.status_code}")
        _sys.exit(1)

    # --- Session required ---
    _app.dependency_overrides.clear()
    _resp7 = _client.get("/api/axis-score-drift-report/srv-001?window_hours=72")
    if _resp7.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {_resp7.status_code}")
        _sys.exit(1)

    print("PASS")
