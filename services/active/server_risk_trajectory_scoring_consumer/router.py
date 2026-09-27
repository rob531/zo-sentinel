# deps: fastapi, pydantic, sqlalchemy
"""Server Risk Trajectory Scoring Consumer.

Consumes axis scores from mcp_llm_axis_scores, computes per-server risk
trajectory classifications (IMPROVING / STABLE / DEGRADING) by comparing the
latest score snapshot against a prior baseline window, and writes results
back to mcp_server_registry.verdict.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM models.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_trajectory_scoring_consumer"])


# --------------------------------------------------------------------------- #
# Enums / constants
# --------------------------------------------------------------------------- #
class Trajectory(str, Enum):
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DEGRADING = "DEGRADING"


# Ordinal value for each tier label; higher ordinal = lower risk
# CRITICAL=0 → highest risk; TRUSTED=6 → lowest risk
_TIER_ORDINAL: dict[str, int] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "MINIMAL": 4,
    "TRUSTED": 6,
    "UNKNOWN": 2,
}
_NEUTRAL_ORDINAL = 2  # fallback when label not mapped


def _ordinal(label: str | None) -> int:
    if label is None:
        return _NEUTRAL_ORDINAL
    return _TIER_ORDINAL.get(label.upper(), _NEUTRAL_ORDINAL)


# Threshold: net ordinal change per axis averaged across axes
# +0.05 per-axis on average → IMPROVING
# -0.05 per-axis on average → DEGRADING
_TRAJECTORY_THRESHOLD = 0.05


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class AxisSnapshot(BaseModel):
    axis_name: str
    label: str | None
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    model_config = ConfigDict(from_attributes=True)


class ServerTrajectoryRecord(BaseModel):
    server_id: str
    trajectory: Trajectory
    net_ordinal_delta: float
    improving_axes: list[str]
    degrading_axes: list[str]
    stable_axes: list[str]
    baseline_snapshot: list[AxisSnapshot]
    current_snapshot: list[AxisSnapshot]
    computed_at: datetime
    lookback_days: int


class BatchTrajectoryResponse(BaseModel):
    processed_count: int
    improving_count: int
    degrading_count: int
    stable_count: int
    insufficient_data_count: int
    tier_distribution: dict[str, int]
    computed_at: datetime


class TrajectorySummary(BaseModel):
    total_servers: int
    improving_count: int
    degrading_count: int
    stable_count: int
    insufficient_data_count: int
    improving_pct: float
    degrading_pct: float
    stable_pct: float


class ConsumerHealth(BaseModel):
    status: str
    total_servers: int
    servers_with_scores: int
    scored_axes_7d: int


# --------------------------------------------------------------------------- #
# Core computation (pure functions – no DB I/O)
# --------------------------------------------------------------------------- #
def _classify_axis(
    cur_label: str | None,
    prev_label: str | None,
) -> tuple[str, float]:
    """
    Classify a single axis change and return (direction, ordinal_delta).

    direction: "improving" | "degrading" | "stable"
    ordinal_delta: ordinal_change (positive = improving)
    """
    cur_ord = _ordinal(cur_label)
    prev_ord = _ordinal(prev_label)
    delta = cur_ord - prev_ord  # positive = moved toward TRUSTED

    if delta > 0:
        return "improving", float(delta)
    if delta < 0:
        return "degrading", float(delta)
    return "stable", 0.0


def _classify_trajectory(axis_deltas: list[tuple[str, float]]) -> Trajectory:
    """
    Aggregate per-axis ordinal deltas into a single trajectory classification.

    axis_deltas: [(axis_name, ordinal_delta), ...]
    """
    if not axis_deltas:
        return Trajectory.STABLE

    total = sum(d for _, d in axis_deltas)
    avg = total / len(axis_deltas)

    if avg > _TRAJECTORY_THRESHOLD:
        return Trajectory.IMPROVING
    if avg < -_TRAJECTORY_THRESHOLD:
        return Trajectory.DEGRADING
    return Trajectory.STABLE


# --------------------------------------------------------------------------- #
# Database helpers (app Postgres via SQLAlchemy ORM)
# --------------------------------------------------------------------------- #
def _latest_scores_by_axis(
    session: Session,
    server_id: str,
) -> dict[str, McpLlmAxisScore]:
    """Latest score row per axis_name for a server."""
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r
    return seen


def _baseline_scores_by_axis(
    session: Session,
    server_id: str,
    cutoff: datetime,
) -> dict[str, McpLlmAxisScore]:
    """Most-recent score row per axis strictly before cutoff."""
    rows = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at < cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r
    return seen


def _compute_server_trajectory(
    session: Session,
    server_id: str,
    lookback_days: int,
    now: datetime,
) -> ServerTrajectoryRecord | None:
    """
    Compute trajectory for one server: compare latest vs. pre-cutoff snapshot.
    Returns None if fewer than 2 snapshots exist.
    """
    cutoff = now - timedelta(days=lookback_days)

    latest = _latest_scores_by_axis(session, server_id)
    baseline = _baseline_scores_by_axis(session, server_id, cutoff)

    if not latest:
        return None

    all_axes = set(latest.keys()) | set(baseline.keys())

    improving_axes: list[str] = []
    degrading_axes: list[str] = []
    stable_axes: list[str] = []
    axis_deltas: list[tuple[str, float]] = []
    baseline_snaps: list[AxisSnapshot] = []
    current_snaps: list[AxisSnapshot] = []

    for axis in sorted(all_axes):
        cur = latest.get(axis)
        prv = baseline.get(axis)

        cur_label = cur.label if cur else None
        prv_label = prv.label if prv else None

        direction, delta = _classify_axis(cur_label, prv_label)
        axis_deltas.append((axis, delta))

        if direction == "improving":
            improving_axes.append(axis)
        elif direction == "degrading":
            degrading_axes.append(axis)
        else:
            stable_axes.append(axis)

        if prv:
            baseline_snaps.append(AxisSnapshot(
                axis_name=axis,
                label=prv.label,
                p_top=prv.p_top,
                p_critical=prv.p_critical,
                p_danger=prv.p_danger,
            ))
        if cur:
            current_snaps.append(AxisSnapshot(
                axis_name=axis,
                label=cur.label,
                p_top=cur.p_top,
                p_critical=cur.p_critical,
                p_danger=cur.p_danger,
            ))

    if len(latest) == 1 and not baseline:
        return None  # insufficient historical data

    trajectory = _classify_trajectory(axis_deltas)
    net_delta = sum(d for _, d in axis_deltas)

    return ServerTrajectoryRecord(
        server_id=server_id,
        trajectory=trajectory,
        net_ordinal_delta=round(net_delta, 4),
        improving_axes=improving_axes,
        degrading_axes=degrading_axes,
        stable_axes=stable_axes,
        baseline_snapshot=baseline_snaps,
        current_snapshot=current_snaps,
        computed_at=now,
        lookback_days=lookback_days,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/health", response_model=ConsumerHealth)
def health(db: Session = Depends(get_session)) -> ConsumerHealth:
    """Return basic health and coverage stats for the consumer."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    scored = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .scalar() or 0
    )
    recent = (
        db.query(func.count(McpLlmAxisScore.id))
        .filter(
            McpLlmAxisScore.scored_at
            >= datetime.now(timezone.utc) - timedelta(days=7)
        )
        .scalar() or 0
    )
    return ConsumerHealth(
        status="ok",
        total_servers=total,
        servers_with_scores=scored,
        scored_axes_7d=recent,
    )


@router.post("/trajectory/compute", response_model=BatchTrajectoryResponse)
def compute_all_trajectories(
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> BatchTrajectoryResponse:
    """
    Batch-compute and persist risk trajectory for all servers that have
    at least one score. Writes the trajectory label + net_delta into
    mcp_server_registry.verdict (as JSON) and returns a summary.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    # Get all servers that have at least one score
    server_ids = [
        r[0]
        for r in db.query(McpLlmAxisScore.server_id).distinct().all()
    ]

    improving_count = 0
    degrading_count = 0
    stable_count = 0
    insufficient_count = 0
    tier_counts: dict[str, int] = {}

    for sid in server_ids:
        record = _compute_server_trajectory(db, sid, lookback_days, now)

        if record is None:
            insufficient_count += 1
            continue

        if record.trajectory == Trajectory.IMPROVING:
            improving_count += 1
        elif record.trajectory == Trajectory.DEGRADING:
            degrading_count += 1
        else:
            stable_count += 1

        # Persist result back to the registry
        verdict_payload = {
            "trajectory": record.trajectory.value,
            "net_ordinal_delta": record.net_ordinal_delta,
            "improving_axes": record.improving_axes,
            "degrading_axes": record.degrading_axes,
            "stable_axes": record.stable_axes,
            "lookback_days": lookback_days,
            "computed_at": now.isoformat(),
        }

        srv = db.get(McpServerRegistry, sid)
        if srv:
            srv.verdict = str(record.trajectory.value)
            srv.verdict_reasoning = str(verdict_payload)
            # Risk tier from the latest overall_risk axis, if available
            overall = next(
                (v for k, v in _latest_scores_by_axis(db, sid).items()
                 if k == "overall_risk"),
                None,
            )
            if overall and overall.label:
                srv.risk_tier = overall.label.upper()
                tier_counts[overall.label.upper()] = (
                    tier_counts.get(overall.label.upper(), 0) + 1
                )
        db.commit()

    return BatchTrajectoryResponse(
        processed_count=len(server_ids),
        improving_count=improving_count,
        degrading_count=degrading_count,
        stable_count=stable_count,
        insufficient_data_count=insufficient_count,
        tier_distribution=tier_counts,
        computed_at=now,
    )


@router.get("/trajectory/{server_id}", response_model=ServerTrajectoryRecord)
def get_server_trajectory(
    server_id: str,
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> ServerTrajectoryRecord:
    """
    Return the computed trajectory record for a single server.
    Compares the latest score snapshot against the most-recent snapshot
    before the lookback cutoff.
    """
    srv = db.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    now = datetime.now(timezone.utc)
    record = _compute_server_trajectory(db, server_id, lookback_days, now)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No trajectory data for server {server_id}. "
                "This server may have fewer than 2 score snapshots "
                "in the last {lookback_days} days."
            ),
        )

    return record


@router.get("/trajectory", response_model=TrajectorySummary)
def get_trajectory_summary(
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> TrajectorySummary:
    """
    Return fleet-wide trajectory summary: counts and percentages per bucket.
    """
    now = datetime.now(timezone.utc)

    server_ids = [
        r[0]
        for r in db.query(McpLlmAxisScore.server_id).distinct().all()
    ]

    improving = degrading = stable = insufficient = 0

    for sid in server_ids:
        record = _compute_server_trajectory(db, sid, lookback_days, now)
        if record is None:
            insufficient += 1
        elif record.trajectory == Trajectory.IMPROVING:
            improving += 1
        elif record.trajectory == Trajectory.DEGRADING:
            degrading += 1
        else:
            stable += 1

    total = len(server_ids) or 1

    return TrajectorySummary(
        total_servers=len(server_ids),
        improving_count=improving,
        degrading_count=degrading,
        stable_count=stable,
        insufficient_data_count=insufficient,
        improving_pct=round(improving / total * 100, 2),
        degrading_pct=round(degrading / total * 100, 2),
        stable_pct=round(stable / total * 100, 2),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json as _json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False,
    )

    from app.models import Base as AppBase
    AppBase.metadata.create_all(test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.now(timezone.utc)
    t_old = now - timedelta(days=60)
    t_cutoff = now - timedelta(days=30)
    t_recent = now - timedelta(days=5)

    with TestSessionLocal() as sess:
        # Register servers
        sess.add(McpServerRegistry(
            server_id="srv-improving",
            name="Improving Server",
            risk_tier="HIGH",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-degrading",
            name="Degrading Server",
            risk_tier="LOW",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-stable",
            name="Stable Server",
            risk_tier="MEDIUM",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-no-scores",
            name="No Scores Server",
            risk_tier=None,
        ))
        sess.commit()

        # srv-improving: HIGH(1) -> MEDIUM(2) on overall_risk = +1 ordinal = IMPROVING
        sess.add(McpLlmAxisScore(
            id=1, server_id="srv-improving", axis_name="overall_risk",
            label="HIGH", p_top=0.80, p_critical=0.15, p_danger=0.05,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=2, server_id="srv-improving", axis_name="overall_risk",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_recent,
        ))
        sess.add(McpLlmAxisScore(
            id=3, server_id="srv-improving", axis_name="auth_strength",
            label="LOW", p_top=0.70, p_critical=0.20, p_danger=0.10,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=4, server_id="srv-improving", axis_name="auth_strength",
            label="MEDIUM", p_top=0.50, p_critical=0.30, p_danger=0.20,
            model_version="v1", scored_at=t_recent,
        ))

        # srv-degrading: MEDIUM(2) -> HIGH(1) on overall_risk = -1 ordinal = DEGRADING
        sess.add(McpLlmAxisScore(
            id=5, server_id="srv-degrading", axis_name="overall_risk",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=6, server_id="srv-degrading", axis_name="overall_risk",
            label="HIGH", p_top=0.80, p_critical=0.15, p_danger=0.05,
            model_version="v1", scored_at=t_recent,
        ))
        sess.add(McpLlmAxisScore(
            id=7, server_id="srv-degrading", axis_name="data_sensitivity",
            label="MEDIUM", p_top=0.50, p_critical=0.30, p_danger=0.20,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=8, server_id="srv-degrading", axis_name="data_sensitivity",
            label="HIGH", p_top=0.75, p_critical=0.18, p_danger=0.07,
            model_version="v1", scored_at=t_recent,
        ))

        # srv-stable: same labels
        sess.add(McpLlmAxisScore(
            id=9, server_id="srv-stable", axis_name="overall_risk",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=10, server_id="srv-stable", axis_name="overall_risk",
            label="MEDIUM", p_top=0.52, p_critical=0.32, p_danger=0.16,
            model_version="v1", scored_at=t_recent,
        ))

        sess.commit()

    client = TestClient(test_app)

    # Test 1: health endpoint
    r = client.get("/api/health")
    if r.status_code != 200:
        print(f"FAIL: health returned {r.status_code}: {r.text}")
        sys.exit(1)
    h = r.json()
    if h["total_servers"] != 4:
        print(f"FAIL: expected 4 total_servers, got {h['total_servers']}")
        sys.exit(1)
    if h["servers_with_scores"] != 3:
        print(f"FAIL: expected 3 servers_with_scores, got {h['servers_with_scores']}")
        sys.exit(1)

    # Test 2: srv-improving → IMPROVING
    r = client.get("/api/trajectory/srv-improving", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: srv-improving returned {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d["trajectory"] != "IMPROVING":
        print(f"FAIL: srv-improving expected IMPROVING, got {d['trajectory']}")
        sys.exit(1)
    if d["net_ordinal_delta"] <= 0:
        print(f"FAIL: net_ordinal_delta should be positive for IMPROVING, got {d['net_ordinal_delta']}")
        sys.exit(1)
    if "overall_risk" not in d["improving_axes"]:
        print(f"FAIL: overall_risk not in improving_axes: {d['improving_axes']}")
        sys.exit(1)

    # Test 3: srv-degrading → DEGRADING
    r = client.get("/api/trajectory/srv-degrading", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: srv-degrading returned {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d["trajectory"] != "DEGRADING":
        print(f"FAIL: srv-degrading expected DEGRADING, got {d['trajectory']}")
        sys.exit(1)
    if d["net_ordinal_delta"] >= 0:
        print(f"FAIL: net_ordinal_delta should be negative for DEGRADING, got {d['net_ordinal_delta']}")
        sys.exit(1)

    # Test 4: srv-stable → STABLE
    r = client.get("/api/trajectory/srv-stable", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: srv-stable returned {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d["trajectory"] != "STABLE":
        print(f"FAIL: srv-stable expected STABLE, got {d['trajectory']}")
        sys.exit(1)

    # Test 5: 404 for server with no scores
    r = client.get("/api/trajectory/srv-no-scores")
    if r.status_code != 404:
        print(f"FAIL: srv-no-scores expected 404, got {r.status_code}")
        sys.exit(1)

    # Test 6: 404 for truly unknown server
    r = client.get("/api/trajectory/nonexistent")
    if r.status_code != 404:
        print(f"FAIL: nonexistent expected 404, got {r.status_code}")
        sys.exit(1)

    # Test 7: fleet summary
    r = client.get("/api/trajectory", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: summary returned {r.status_code}: {r.text}")
        sys.exit(1)
    s = r.json()
    if s["total_servers"] != 3:
        print(f"FAIL: expected 3 total_servers in summary, got {s['total_servers']}")
        sys.exit(1)
    if s["improving_count"] != 1:
        print(f"FAIL: expected 1 improving_count, got {s['improving_count']}")
        sys.exit(1)
    if s["degrading_count"] != 1:
        print(f"FAIL: expected 1 degrading_count, got {s['degrading_count']}")
        sys.exit(1)
    if s["stable_count"] != 1:
        print(f"FAIL: expected 1 stable_count, got {s['stable_count']}")
        sys.exit(1)

    # Test 8: batch compute
    r = client.post("/api/trajectory/compute", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: compute returned {r.status_code}: {r.text}")
        sys.exit(1)
    b = r.json()
    if b["processed_count"] != 3:
        print(f"FAIL: processed_count expected 3, got {b['processed_count']}")
        sys.exit(1)
    if b["improving_count"] != 1:
        print(f"FAIL: batch improving_count expected 1, got {b['improving_count']}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
