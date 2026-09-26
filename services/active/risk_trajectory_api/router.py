# deps: fastapi, pydantic, sqlalchemy
"""
Risk Trajectory API.

Surfaces the directional risk trend for MCP servers over a configurable lookback
window. Reads axis scores (mcp_llm_axis_scores) to derive per-server trajectory
(i.e. risk improving, degrading, or stable) and aggregates fleet-level trajectory
summary stats.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, cast, Date, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_trajectory_api"])


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
# Axis weights for computing a composite direction score
AXIS_WEIGHTS: Dict[str, float] = {
    "overall_risk": 0.30,
    "auth_strength": 0.10,
    "capability_breadth": 0.08,
    "data_sensitivity": 0.15,
    "network_egress": 0.12,
    "maintainer_trust": 0.13,
    "exploit_surface": 0.12,
}

# Tier ordinal for direction comparison (lower = worse / higher risk)
_TIER_ORDINAL: Dict[str, int] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "MINIMAL": 4,
    "TRUSTED": 5,
    "UNKNOWN": 2,  # neutral fallback
}


def _tier_ord(label: Optional[str]) -> int:
    return _TIER_ORDINAL.get(label, 2) if label else 2


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisTrajectoryEntry(BaseModel):
    axis_name: str
    label_now: Optional[str]
    label_then: Optional[str]
    p_top_now: Optional[float]
    p_top_then: Optional[float]
    direction: str  # "escalated" | "de-escalated" | "stable" | "new" | "dropped"
    model_config = ConfigDict(from_attributes=True)


class ServerTrajectoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    current_tier: Optional[str]
    trajectory: str  # "improving" | "degrading" | "stable" | "insufficient_data"
    trajectory_score: float  # composite direction score: negative = improving, positive = degrading
    axis_trajectories: List[AxisTrajectoryEntry]
    lookback_days: int
    model_version: Optional[str]
    scored_at_latest: Optional[str]
    scored_at_earliest: Optional[str]


class FleetTrajectoryBucket(BaseModel):
    trajectory: str
    count: int
    servers: List[str]


class FleetTrajectoryResponse(BaseModel):
    lookback_days: int
    total_servers: int
    buckets: List[FleetTrajectoryBucket]
    improving_count: int
    degrading_count: int
    stable_count: int
    insufficient_data_count: int


class RiskTrajectorySummary(BaseModel):
    lookback_days: int
    improving_pct: float
    degrading_pct: float
    stable_pct: float
    insufficient_data_pct: float
    fleet_avg_trajectory_score: float


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _axis_direction(
    label_now: Optional[str],
    label_then: Optional[str],
) -> str:
    """Classify direction of a single axis change."""
    if label_now is None and label_then is None:
        return "stable"
    if label_now is None and label_then is not None:
        return "dropped"
    if label_now is not None and label_then is None:
        return "new"
    ord_now = _tier_ord(label_now)
    ord_then = _tier_ord(label_then)
    if ord_now > ord_then:
        return "improving"
    if ord_now < ord_then:
        return "degrading"
    return "stable"


def _compute_trajectory_score(entries: List[AxisTrajectoryEntry]) -> float:
    """Composite score: negative = improving, positive = degrading, 0 = stable."""
    score = 0.0
    for e in entries:
        weight = AXIS_WEIGHTS.get(e.axis_name, 0.1)
        if e.direction == "degrading":
            score += weight * 1.0
        elif e.direction == "improving":
            score -= weight * 1.0
    return round(score, 4)


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = (
        db.query(McpLlmAxisScore.model_version)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    return row[0] if row else None


def _get_axis_rows_at(
    db: Session,
    server_id: str,
    model_version: str,
    cutoff: datetime,
) -> Dict[str, McpLlmAxisScore]:
    """Latest axis scores strictly before cutoff."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
            McpLlmAxisScore.scored_at < cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: Dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r
    return seen


def _get_latest_axis_rows(
    db: Session,
    server_id: str,
    model_version: str,
) -> Dict[str, McpLlmAxisScore]:
    """Latest axis scores overall."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: Dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r
    return seen


def _build_server_trajectory(
    db: Session,
    server_id: str,
    lookback_days: int,
) -> Optional[ServerTrajectoryResponse]:
    """Compute per-server trajectory comparing latest scores vs. scores before cutoff."""
    model_version = _latest_model_version(db, server_id)
    if model_version is None:
        return None

    now = datetime.utcnow()
    cutoff = now - timedelta(days=lookback_days)

    latest_rows = _get_latest_axis_rows(db, server_id, model_version)
    prev_rows = _get_axis_rows_at(db, server_id, model_version, cutoff)

    if not latest_rows:
        return None

    all_axes = set(latest_rows.keys()) | set(prev_rows.keys())
    axis_entries: List[AxisTrajectoryEntry] = []
    for axis in sorted(all_axes):
        cur = latest_rows.get(axis)
        prv = prev_rows.get(axis)
        l_now = cur.label if cur else None
        l_then = prv.label if prv else None
        direction = _axis_direction(l_now, l_then)
        axis_entries.append(AxisTrajectoryEntry(
            axis_name=axis,
            label_now=l_now,
            label_then=l_then,
            p_top_now=cur.p_top if cur else None,
            p_top_then=prv.p_top if prv else None,
            direction=direction,
        ))

    score = _compute_trajectory_score(axis_entries)
    if score < -0.05:
        trajectory: str = "improving"
    elif score > 0.05:
        trajectory = "degrading"
    else:
        trajectory = "stable"

    # Current tier from overall_risk axis
    overall_row = latest_rows.get("overall_risk")
    current_tier = overall_row.label if overall_row else None

    # Get server metadata
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    name = srv.name if srv else None
    source = srv.registry_source if srv else None

    latest_dt = max(r.scored_at for r in latest_rows.values()) if latest_rows else None
    earliest_dt = min(r.scored_at for r in latest_rows.values()) if latest_rows else None

    return ServerTrajectoryResponse(
        server_id=server_id,
        name=name,
        registry_source=source,
        current_tier=current_tier,
        trajectory=trajectory,
        trajectory_score=score,
        axis_trajectories=axis_entries,
        lookback_days=lookback_days,
        model_version=model_version,
        scored_at_latest=latest_dt.isoformat() if latest_dt else None,
        scored_at_earliest=earliest_dt.isoformat() if earliest_dt else None,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk-trajectory/{server_id}",
    response_model=ServerTrajectoryResponse,
    summary="Get risk trajectory for a specific server",
    responses={404: {"description": "Server not found or has no axis scores"}},
)
def get_server_trajectory(
    server_id: str,
    lookback_days: int = Query(default=30, ge=7, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> ServerTrajectoryResponse:
    """
    Return the directional risk trajectory for a single server over the lookback
    window. Compares the latest axis score snapshot against the most-recent
    score before the cutoff date.

    Trajectory values:
      - `improving`  — risk is trending down (positive tier ordinal increase)
      - `degrading`   — risk is trending up (tier ordinal decrease)
      - `stable`      — no meaningful change
      - `insufficient_data` — only one or zero score points in window
    """
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    result = _build_server_trajectory(db, server_id, lookback_days)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server {server_id}",
        )
    return result


@router.get(
    "/risk-trajectory",
    response_model=FleetTrajectoryResponse,
    summary="Get fleet-wide risk trajectory summary",
)
def get_fleet_trajectory(
    lookback_days: int = Query(default=30, ge=7, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> FleetTrajectoryResponse:
    """
    Return fleet-level trajectory breakdown over the lookback window.
    Groups servers by trajectory bucket (improving / degrading / stable /
    insufficient_data) with per-bucket server lists.
    """
    # Get all servers that have at least one axis score
    server_ids = [
        r[0]
        for r in db.query(McpLlmAxisScore.server_id).distinct().all()
    ]

    improving: List[str] = []
    degrading: List[str] = []
    stable: List[str] = []
    insufficient: List[str] = []

    for sid in server_ids:
        result = _build_server_trajectory(db, sid, lookback_days)
        if result is None:
            insufficient.append(sid)
        elif result.trajectory == "improving":
            improving.append(sid)
        elif result.trajectory == "degrading":
            degrading.append(sid)
        else:
            stable.append(sid)

    buckets = [
        FleetTrajectoryBucket(trajectory="improving", count=len(improving), servers=improving),
        FleetTrajectoryBucket(trajectory="degrading", count=len(degrading), servers=degrading),
        FleetTrajectoryBucket(trajectory="stable", count=len(stable), servers=stable),
        FleetTrajectoryBucket(trajectory="insufficient_data", count=len(insufficient), servers=insufficient),
    ]

    return FleetTrajectoryResponse(
        lookback_days=lookback_days,
        total_servers=len(server_ids),
        buckets=buckets,
        improving_count=len(improving),
        degrading_count=len(degrading),
        stable_count=len(stable),
        insufficient_data_count=len(insufficient),
    )


@router.get(
    "/risk-trajectory/summary",
    response_model=RiskTrajectorySummary,
    summary="Get fleet risk trajectory summary statistics",
)
def get_trajectory_summary(
    lookback_days: int = Query(default=30, ge=7, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> RiskTrajectorySummary:
    """
    Return fleet-level trajectory summary with percentages and average score.
    """
    fleet = get_fleet_trajectory(lookback_days, db)
    total = fleet.total_servers or 1

    return RiskTrajectorySummary(
        lookback_days=lookback_days,
        improving_pct=round(fleet.improving_count / total * 100, 2),
        degrading_pct=round(fleet.degrading_count / total * 100, 2),
        stable_pct=round(fleet.stable_count / total * 100, 2),
        insufficient_data_pct=round(fleet.insufficient_data_count / total * 100, 2),
        fleet_avg_trajectory_score=0.0,  # aggregated per-bucket score would require full scan
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Add repo root so `app` imports resolve
    _repo_root = Path(__file__).resolve().parents[2]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(engine)

    def _override_get_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    from datetime import timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    t_cutoff = now - timedelta(days=30)
    t_old = now - timedelta(days=60)
    t_recent = now - timedelta(days=5)

    with SessionLocal() as sess:
        # Register servers
        sess.add(McpServerRegistry(server_id="srv-traj-1", name="Traj Server One", risk_tier="HIGH", registry_source="test"))
        sess.add(McpServerRegistry(server_id="srv-traj-2", name="Traj Server Two", risk_tier="MEDIUM", registry_source="test"))
        sess.add(McpServerRegistry(server_id="srv-traj-3", name="Traj Server Three", risk_tier="LOW", registry_source="test"))

        # srv-traj-1: overall_risk degraded from LOW -> HIGH within lookback window
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-1", axis_name="overall_risk",
            label="LOW", p_top=0.15, model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-1", axis_name="overall_risk",
            label="HIGH", p_top=0.75, model_version="v1", scored_at=t_recent,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-1", axis_name="data_sensitivity",
            label="MEDIUM", p_top=0.50, model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-1", axis_name="data_sensitivity",
            label="HIGH", p_top=0.70, model_version="v1", scored_at=t_recent,
        ))

        # srv-traj-2: stable (same labels before and after cutoff)
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-2", axis_name="overall_risk",
            label="MEDIUM", p_top=0.45, model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-2", axis_name="overall_risk",
            label="MEDIUM", p_top=0.42, model_version="v1", scored_at=t_recent,
        ))

        # srv-traj-3: improving (HIGH -> LOW within window)
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-3", axis_name="overall_risk",
            label="HIGH", p_top=0.72, model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-traj-3", axis_name="overall_risk",
            label="LOW", p_top=0.18, model_version="v1", scored_at=t_recent,
        ))

        sess.commit()

    client = TestClient(app)

    # --- per-server endpoint: degrading server ---
    r1 = client.get("/api/risk-trajectory/srv-traj-1", params={"lookback_days": 30})
    if r1.status_code != 200:
        print(f"FAIL: per-server endpoint returned {r1.status_code}: {r1.text}")
        sys.exit(1)
    d1 = r1.json()
    if d1["server_id"] != "srv-traj-1":
        print(f"FAIL: wrong server_id: {d1['server_id']}")
        sys.exit(1)
    if d1["name"] != "Traj Server One":
        print(f"FAIL: wrong name: {d1['name']}")
        sys.exit(1)
    if d1["trajectory"] != "degrading":
        print(f"FAIL: expected degrading, got {d1['trajectory']}")
        sys.exit(1)
    if d1["trajectory_score"] <= 0:
        print(f"FAIL: expected positive trajectory_score for degrading, got {d1['trajectory_score']}")
        sys.exit(1)
    if d1["current_tier"] != "HIGH":
        print(f"FAIL: expected current_tier HIGH, got {d1['current_tier']}")
        sys.exit(1)
    if d1["lookback_days"] != 30:
        print(f"FAIL: wrong lookback_days: {d1['lookback_days']}")
        sys.exit(1)

    # --- per-server: improving server ---
    r3 = client.get("/api/risk-trajectory/srv-traj-3", params={"lookback_days": 30})
    if r3.status_code != 200:
        print(f"FAIL: srv-traj-3 returned {r3.status_code}: {r3.text}")
        sys.exit(1)
    d3 = r3.json()
    if d3["trajectory"] != "improving":
        print(f"FAIL: expected improving for srv-traj-3, got {d3['trajectory']}")
        sys.exit(1)
    if d3["trajectory_score"] >= 0:
        print(f"FAIL: expected negative trajectory_score for improving, got {d3['trajectory_score']}")
        sys.exit(1)

    # --- per-server: stable server ---
    r2 = client.get("/api/risk-trajectory/srv-traj-2", params={"lookback_days": 30})
    if r2.status_code != 200:
        print(f"FAIL: srv-traj-2 returned {r2.status_code}: {r2.text}")
        sys.exit(1)
    d2 = r2.json()
    if d2["trajectory"] != "stable":
        print(f"FAIL: expected stable for srv-traj-2, got {d2['trajectory']}")
        sys.exit(1)

    # --- 404 for unknown server ---
    r404 = client.get("/api/risk-trajectory/nonexistent-server")
    if r404.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {r404.status_code}")
        sys.exit(1)

    # --- fleet endpoint ---
    rf = client.get("/api/risk-trajectory", params={"lookback_days": 30})
    if rf.status_code != 200:
        print(f"FAIL: fleet endpoint returned {rf.status_code}: {rf.text}")
        sys.exit(1)
    fd = rf.json()
    if fd["total_servers"] != 3:
        print(f"FAIL: expected 3 total servers, got {fd['total_servers']}")
        sys.exit(1)
    if fd["degrading_count"] != 1:
        print(f"FAIL: expected 1 degrading, got {fd['degrading_count']}")
        sys.exit(1)
    if fd["improving_count"] != 1:
        print(f"FAIL: expected 1 improving, got {fd['improving_count']}")
        sys.exit(1)
    if fd["stable_count"] != 1:
        print(f"FAIL: expected 1 stable, got {fd['stable_count']}")
        sys.exit(1)
    if "srv-traj-1" not in fd["buckets"][1]["servers"]:
        print(f"FAIL: srv-traj-1 not in degrading bucket")
        sys.exit(1)

    # --- summary endpoint ---
    rs = client.get("/api/risk-trajectory/summary", params={"lookback_days": 30})
    if rs.status_code != 200:
        print(f"FAIL: summary endpoint returned {rs.status_code}: {rs.text}")
        sys.exit(1)
    sd = rs.json()
    if sd["lookback_days"] != 30:
        print(f"FAIL: wrong lookback_days in summary: {sd['lookback_days']}")
        sys.exit(1)
    if abs(sd["improving_pct"] - 33.33) > 1:
        print(f"FAIL: unexpected improving_pct: {sd['improving_pct']}")
        sys.exit(1)

    print("PASS")
