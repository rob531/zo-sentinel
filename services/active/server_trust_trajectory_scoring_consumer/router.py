# deps: fastapi, pydantic, sqlalchemy
"""Server Trust Trajectory Scoring Consumer.

Consumes axis scores (esp. maintainer_trust) from mcp_llm_axis_scores,
computes per-server TRUST TRAJECTORY classifications (TRUSTED / FLAT /
UNTRUSTED) by comparing the latest trust snapshot against a prior baseline
window, and writes structured results back to mcp_server_registry.meta.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM models.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_trust_trajectory_scoring_consumer"])


# --------------------------------------------------------------------------- #
# Enums / constants
# --------------------------------------------------------------------------- #
class TrustTrajectory(str, Enum):
    TRUSTED = "TRUSTED"    # maintainer_trust ordinal increased
    FLAT = "FLAT"          # no meaningful change
    UNTRUSTED = "UNTRUSTED"  # maintainer_trust ordinal decreased


# Ordinal for maintainer_trust labels; higher = more trusted
# UNKNOWN=0, NONE=1, LOW=2, MEDIUM=3, HIGH=4, ESTABLISHED=5, VERIFIED=6
_TRUST_ORDINAL: dict[str, int] = {
    "UNKNOWN": 0,
    "NONE": 1,
    "LOW": 2,
    "MEDIUM": 3,
    "HIGH": 4,
    "ESTABLISHED": 5,
    "VERIFIED": 6,
}
_NEUTRAL_ORDINAL = 2  # fallback for unknown labels


def _trust_ordinal(label: str | None) -> int:
    if label is None:
        return _NEUTRAL_ORDINAL
    return _TRUST_ORDINAL.get(label.upper(), _NEUTRAL_ORDINAL)


# Threshold: ordinal delta per axis averaged across axes
# +0.05 per-axis → TRUSTED
# -0.05 per-axis → UNTRUSTED
_TRAJECTORY_THRESHOLD = 0.05


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class TrustAxisSnapshot(BaseModel):
    axis_name: str
    label: str | None
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    model_config = ConfigDict(from_attributes=True)


class TrustTrajectoryRecord(BaseModel):
    server_id: str
    trajectory: TrustTrajectory
    net_trust_delta: float
    improving_axes: list[str]
    degrading_axes: list[str]
    stable_axes: list[str]
    baseline_snapshot: list[TrustAxisSnapshot]
    current_snapshot: list[TrustAxisSnapshot]
    computed_at: datetime
    lookback_days: int


class BatchTrustTrajectoryResponse(BaseModel):
    processed_count: int
    trusted_count: int
    flat_count: int
    untrusted_count: int
    insufficient_data_count: int
    computed_at: datetime


class TrustTrajectorySummary(BaseModel):
    total_servers: int
    trusted_count: int
    flat_count: int
    untrusted_count: int
    insufficient_data_count: int
    trusted_pct: float
    flat_pct: float
    untrusted_pct: float


class ConsumerHealth(BaseModel):
    status: str
    total_servers: int
    servers_with_trust_scores: int
    scored_axes_7d: int


# --------------------------------------------------------------------------- #
# Core computation (pure functions – no DB I/O)
# --------------------------------------------------------------------------- #
def _classify_trust_axis(
    cur_label: str | None,
    prev_label: str | None,
) -> tuple[str, float]:
    """Return (direction, ordinal_delta) for a single axis change."""
    cur_ord = _trust_ordinal(cur_label)
    prev_ord = _trust_ordinal(prev_label)
    delta = cur_ord - prev_ord
    if delta > 0:
        return "improving", float(delta)
    if delta < 0:
        return "degrading", float(delta)
    return "stable", 0.0


def _classify_trust_trajectory(axis_deltas: list[tuple[str, float]]) -> TrustTrajectory:
    """Aggregate per-axis ordinal deltas into a trust trajectory."""
    if not axis_deltas:
        return TrustTrajectory.FLAT
    total = sum(d for _, d in axis_deltas)
    avg = total / len(axis_deltas)
    if avg > _TRAJECTORY_THRESHOLD:
        return TrustTrajectory.TRUSTED
    if avg < -_TRAJECTORY_THRESHOLD:
        return TrustTrajectory.UNTRUSTED
    return TrustTrajectory.FLAT


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


def _compute_server_trust_trajectory(
    session: Session,
    server_id: str,
    lookback_days: int,
    now: datetime,
) -> TrustTrajectoryRecord | None:
    """
    Compute trust trajectory for one server: compare latest vs. pre-cutoff snapshot.
    Returns None if no scores exist.
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
    baseline_snaps: list[TrustAxisSnapshot] = []
    current_snaps: list[TrustAxisSnapshot] = []

    for axis in sorted(all_axes):
        cur = latest.get(axis)
        prv = baseline.get(axis)

        cur_label = cur.label if cur else None
        prv_label = prv.label if prv else None

        direction, delta = _classify_trust_axis(cur_label, prv_label)
        axis_deltas.append((axis, delta))

        if direction == "improving":
            improving_axes.append(axis)
        elif direction == "degrading":
            degrading_axes.append(axis)
        else:
            stable_axes.append(axis)

        if prv:
            baseline_snaps.append(TrustAxisSnapshot(
                axis_name=axis,
                label=prv.label,
                p_top=prv.p_top,
                p_critical=prv.p_critical,
                p_danger=prv.p_danger,
            ))
        if cur:
            current_snaps.append(TrustAxisSnapshot(
                axis_name=axis,
                label=cur.label,
                p_top=cur.p_top,
                p_critical=cur.p_critical,
                p_danger=cur.p_danger,
            ))

    if len(latest) == 1 and not baseline:
        return None  # insufficient historical data

    trajectory = _classify_trust_trajectory(axis_deltas)
    net_delta = sum(d for _, d in axis_deltas)

    return TrustTrajectoryRecord(
        server_id=server_id,
        trajectory=trajectory,
        net_trust_delta=round(net_delta, 4),
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
        servers_with_trust_scores=scored,
        scored_axes_7d=recent,
    )


@router.post("/trust-trajectory/compute", response_model=BatchTrustTrajectoryResponse)
def compute_all_trust_trajectories(
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> BatchTrustTrajectoryResponse:
    """
    Batch-compute and persist trust trajectory for all servers with scores.
    Writes structured trajectory metadata into mcp_server_registry.meta.
    """
    now = datetime.now(timezone.utc)

    server_ids = [
        r[0]
        for r in db.query(McpLlmAxisScore.server_id).distinct().all()
    ]

    trusted_count = 0
    flat_count = 0
    untrusted_count = 0
    insufficient_count = 0

    for sid in server_ids:
        record = _compute_server_trust_trajectory(db, sid, lookback_days, now)

        if record is None:
            insufficient_count += 1
            continue

        if record.trajectory == TrustTrajectory.TRUSTED:
            trusted_count += 1
        elif record.trajectory == TrustTrajectory.UNTRUSTED:
            untrusted_count += 1
        else:
            flat_count += 1

        # Persist result into mcp_server_registry.meta
        meta_payload = {
            "trust_trajectory": record.trajectory.value,
            "net_trust_delta": record.net_trust_delta,
            "improving_axes": record.improving_axes,
            "degrading_axes": record.degrading_axes,
            "stable_axes": record.stable_axes,
            "lookback_days": lookback_days,
            "computed_at": now.isoformat(),
        }

        srv = db.get(McpServerRegistry, sid)
        if srv:
            # Merge into existing meta dict rather than clobber
            existing = {}
            if srv.meta:
                if isinstance(srv.meta, dict):
                    existing = srv.meta
                elif isinstance(srv.meta, str):
                    try:
                        existing = json.loads(srv.meta)
                    except Exception:
                        existing = {}
            existing["trust_trajectory"] = meta_payload
            srv.meta = existing
        db.commit()

    return BatchTrustTrajectoryResponse(
        processed_count=len(server_ids),
        trusted_count=trusted_count,
        flat_count=flat_count,
        untrusted_count=untrusted_count,
        insufficient_data_count=insufficient_count,
        computed_at=now,
    )


@router.get("/trust-trajectory/{server_id}", response_model=TrustTrajectoryRecord)
def get_server_trust_trajectory(
    server_id: str,
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> TrustTrajectoryRecord:
    """
    Return the trust trajectory record for a single server.
    Compares the latest score snapshot against the most-recent snapshot
    before the lookback cutoff.
    """
    srv = db.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    now = datetime.now(timezone.utc)
    record = _compute_server_trust_trajectory(db, server_id, lookback_days, now)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No trust trajectory data for server {server_id}. "
                f"This server may have fewer than 2 score snapshots "
                f"in the last {lookback_days} days."
            ),
        )

    return record


@router.get("/trust-trajectory", response_model=TrustTrajectorySummary)
def get_trust_trajectory_summary(
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> TrustTrajectorySummary:
    """
    Return fleet-wide trust trajectory summary: counts and percentages per bucket.
    """
    now = datetime.now(timezone.utc)

    server_ids = [
        r[0]
        for r in db.query(McpLlmAxisScore.server_id).distinct().all()
    ]

    trusted = flat = untrusted = insufficient = 0

    for sid in server_ids:
        record = _compute_server_trust_trajectory(db, sid, lookback_days, now)
        if record is None:
            insufficient += 1
        elif record.trajectory == TrustTrajectory.TRUSTED:
            trusted += 1
        elif record.trajectory == TrustTrajectory.UNTRUSTED:
            untrusted += 1
        else:
            flat += 1

    total = len(server_ids) or 1

    return TrustTrajectorySummary(
        total_servers=len(server_ids),
        trusted_count=trusted,
        flat_count=flat,
        untrusted_count=untrusted,
        insufficient_data_count=insufficient,
        trusted_pct=round(trusted / total * 100, 2),
        flat_pct=round(flat / total * 100, 2),
        untrusted_pct=round(untrusted / total * 100, 2),
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
    t_recent = now - timedelta(days=5)

    with TestSessionLocal() as sess:
        # Register servers
        sess.add(McpServerRegistry(
            server_id="srv-trusted",
            name="Trusted Server",
            trust_score=80.0,
        ))
        sess.add(McpServerRegistry(
            server_id="srv-untrusted",
            name="Untrusted Server",
            trust_score=20.0,
        ))
        sess.add(McpServerRegistry(
            server_id="srv-flat",
            name="Flat Server",
            trust_score=50.0,
        ))
        sess.add(McpServerRegistry(
            server_id="srv-no-scores",
            name="No Scores Server",
            trust_score=None,
        ))
        sess.commit()

        # srv-trusted: NONE(1) -> VERIFIED(6) on maintainer_trust = +5 ordinal = TRUSTED
        sess.add(McpLlmAxisScore(
            id=1, server_id="srv-trusted", axis_name="maintainer_trust",
            label="NONE", p_top=0.80, p_critical=0.15, p_danger=0.05,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=2, server_id="srv-trusted", axis_name="maintainer_trust",
            label="VERIFIED", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_recent,
        ))
        sess.add(McpLlmAxisScore(
            id=3, server_id="srv-trusted", axis_name="auth_strength",
            label="LOW", p_top=0.70, p_critical=0.20, p_danger=0.10,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=4, server_id="srv-trusted", axis_name="auth_strength",
            label="MEDIUM", p_top=0.50, p_critical=0.30, p_danger=0.20,
            model_version="v1", scored_at=t_recent,
        ))

        # srv-untrusted: VERIFIED(6) -> NONE(1) on maintainer_trust = -5 ordinal = UNTRUSTED
        sess.add(McpLlmAxisScore(
            id=5, server_id="srv-untrusted", axis_name="maintainer_trust",
            label="VERIFIED", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=6, server_id="srv-untrusted", axis_name="maintainer_trust",
            label="NONE", p_top=0.80, p_critical=0.15, p_danger=0.05,
            model_version="v1", scored_at=t_recent,
        ))
        sess.add(McpLlmAxisScore(
            id=7, server_id="srv-untrusted", axis_name="exploit_surface",
            label="HIGH", p_top=0.50, p_critical=0.30, p_danger=0.20,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=8, server_id="srv-untrusted", axis_name="exploit_surface",
            label="LOW", p_top=0.75, p_critical=0.18, p_danger=0.07,
            model_version="v1", scored_at=t_recent,
        ))

        # srv-flat: same labels
        sess.add(McpLlmAxisScore(
            id=9, server_id="srv-flat", axis_name="maintainer_trust",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=10, server_id="srv-flat", axis_name="maintainer_trust",
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
    if h["servers_with_trust_scores"] != 3:
        print(f"FAIL: expected 3 servers_with_trust_scores, got {h['servers_with_trust_scores']}")
        sys.exit(1)

    # Test 2: srv-trusted → TRUSTED
    r = client.get("/api/trust-trajectory/srv-trusted", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: srv-trusted returned {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d["trajectory"] != "TRUSTED":
        print(f"FAIL: srv-trusted expected TRUSTED, got {d['trajectory']}")
        sys.exit(1)
    if d["net_trust_delta"] <= 0:
        print(f"FAIL: net_trust_delta should be positive for TRUSTED, got {d['net_trust_delta']}")
        sys.exit(1)
    if "maintainer_trust" not in d["improving_axes"]:
        print(f"FAIL: maintainer_trust not in improving_axes: {d['improving_axes']}")
        sys.exit(1)

    # Test 3: srv-untrusted → UNTRUSTED
    r = client.get("/api/trust-trajectory/srv-untrusted", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: srv-untrusted returned {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d["trajectory"] != "UNTRUSTED":
        print(f"FAIL: srv-untrusted expected UNTRUSTED, got {d['trajectory']}")
        sys.exit(1)
    if d["net_trust_delta"] >= 0:
        print(f"FAIL: net_trust_delta should be negative for UNTRUSTED, got {d['net_trust_delta']}")
        sys.exit(1)

    # Test 4: srv-flat → FLAT
    r = client.get("/api/trust-trajectory/srv-flat", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: srv-flat returned {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d["trajectory"] != "FLAT":
        print(f"FAIL: srv-flat expected FLAT, got {d['trajectory']}")
        sys.exit(1)

    # Test 5: 404 for server with no scores
    r = client.get("/api/trust-trajectory/srv-no-scores")
    if r.status_code != 404:
        print(f"FAIL: srv-no-scores expected 404, got {r.status_code}")
        sys.exit(1)

    # Test 6: 404 for truly unknown server
    r = client.get("/api/trust-trajectory/nonexistent")
    if r.status_code != 404:
        print(f"FAIL: nonexistent expected 404, got {r.status_code}")
        sys.exit(1)

    # Test 7: fleet summary
    r = client.get("/api/trust-trajectory", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: summary returned {r.status_code}: {r.text}")
        sys.exit(1)
    s = r.json()
    if s["total_servers"] != 3:
        print(f"FAIL: expected 3 total_servers in summary, got {s['total_servers']}")
        sys.exit(1)
    if s["trusted_count"] != 1:
        print(f"FAIL: expected 1 trusted_count, got {s['trusted_count']}")
        sys.exit(1)
    if s["untrusted_count"] != 1:
        print(f"FAIL: expected 1 untrusted_count, got {s['untrusted_count']}")
        sys.exit(1)
    if s["flat_count"] != 1:
        print(f"FAIL: expected 1 flat_count, got {s['flat_count']}")
        sys.exit(1)

    # Test 8: batch compute
    r = client.post("/api/trust-trajectory/compute", params={"lookback_days": 30})
    if r.status_code != 200:
        print(f"FAIL: compute returned {r.status_code}: {r.text}")
        sys.exit(1)
    b = r.json()
    if b["processed_count"] != 3:
        print(f"FAIL: processed_count expected 3, got {b['processed_count']}")
        sys.exit(1)
    if b["trusted_count"] != 1:
        print(f"FAIL: batch trusted_count expected 1, got {b['trusted_count']}")
        sys.exit(1)
    if b["untrusted_count"] != 1:
        print(f"FAIL: batch untrusted_count expected 1, got {b['untrusted_count']}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
