# deps: fastapi, pydantic, sqlalchemy
"""Server Score Evolution API.

Tracks how server axis scores and risk tiers evolve over time — transitions,
velocity, and trajectory.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_score_evolution_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EvolutionEntry(BaseModel):
    scored_at: datetime
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: str

    model_config = ConfigDict(from_attributes=True)


class AxisTrajectory(BaseModel):
    axis_name: str
    first_label: Optional[str]
    last_label: Optional[str]
    first_p_top: Optional[float]
    last_p_top: Optional[float]
    first_scored_at: Optional[datetime]
    last_scored_at: Optional[datetime]
    label_changed: bool
    escalated_now: bool


class ServerScoreEvolutionResponse(BaseModel):
    server_id: str
    name: Optional[str]
    period_days: int
    total_score_events: int
    trajectories: list[AxisTrajectory]
    scores: list[EvolutionEntry]


class TierTransition(BaseModel):
    axis_name: str
    from_label: Optional[str]
    to_label: Optional[str]
    from_p_top: Optional[float]
    to_p_top: Optional[float]
    scored_at: datetime
    direction: str  # "up", "down", "unchanged"


class TransitionSummary(BaseModel):
    axis_name: str
    transition_count: int
    escalations: int
    improvements: int
    regressions: int


class ServerTierTransitionsResponse(BaseModel):
    server_id: str
    name: Optional[str]
    period_days: int
    transitions: list[TierTransition]
    summary: list[TransitionSummary]


class VelocityPoint(BaseModel):
    date: str
    axis_name: str
    label: str
    p_top: float
    delta_p_top: Optional[float]


class ServerScoreVelocityResponse(BaseModel):
    server_id: str
    name: Optional[str]
    period_days: int
    velocity: list[VelocityPoint]


class AxisCorrelation(BaseModel):
    axis_a: str
    axis_b: str
    correlation: str  # "aligned", "opposing", "independent"


class ServerCorrelationMatrixResponse(BaseModel):
    server_id: str
    name: Optional[str]
    correlations: list[AxisCorrelation]


ALL_AXES = frozenset({
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
})


def _direction(from_label: Optional[str], to_label: Optional[str]) -> str:
    if from_label is None or to_label is None:
        return "unchanged"
    order = ["MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    try:
        fi = order.index(from_label.upper())
        ti = order.index(to_label.upper())
    except ValueError:
        return "unchanged"
    if ti > fi:
        return "up"
    if ti < fi:
        return "down"
    return "unchanged"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/server-score-evolution/{server_id}",
    response_model=ServerScoreEvolutionResponse,
    summary="Get score evolution trajectory for a server",
    responses={404: {"description": "Server not found"}},
)
def get_score_evolution(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerScoreEvolutionResponse:
    """Return per-axis trajectory (first→last) and all score events in the period."""
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.asc())
        .all()
    )

    # Build trajectories per axis
    axis_data: dict[str, list[McpLlmAxisScore]] = {}
    for r in rows:
        axis_data.setdefault(r.axis_name, []).append(r)

    trajectories = []
    for axis_name, axis_rows in sorted(axis_data.items()):
        first = axis_rows[0]
        last = axis_rows[-1]
        trajectories.append(AxisTrajectory(
            axis_name=axis_name,
            first_label=first.label,
            last_label=last.label,
            first_p_top=first.p_top,
            last_p_top=last.p_top,
            first_scored_at=first.scored_at,
            last_scored_at=last.scored_at,
            label_changed=(first.label or None) != (last.label or None),
            escalated_now=bool(last.escalated),
        ))

    scores = [
        EvolutionEntry(
            scored_at=r.scored_at,
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            model_version=r.model_version or "",
        )
        for r in rows
    ]

    return ServerScoreEvolutionResponse(
        server_id=server_id,
        name=srv.name,
        period_days=period_days,
        total_score_events=len(rows),
        trajectories=trajectories,
        scores=scores,
    )


@router.get(
    "/server-score-evolution/{server_id}/transitions",
    response_model=ServerTierTransitionsResponse,
    summary="Get tier transitions for a server",
    responses={404: {"description": "Server not found"}},
)
def get_tier_transitions(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerTierTransitionsResponse:
    """Return label transitions between consecutive scores per axis."""
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.asc())
        .all()
    )

    # Group by axis
    by_axis: dict[str, list[McpLlmAxisScore]] = {}
    for r in rows:
        by_axis.setdefault(r.axis_name, []).append(r)

    transitions = []
    summary_map: dict[str, dict[str, int]] = {}

    for axis_name, axis_rows in sorted(by_axis.items()):
        summary_map[axis_name] = {"transitions": 0, "escalations": 0, "improvements": 0, "regressions": 0}
        for i in range(1, len(axis_rows)):
            prev, curr = axis_rows[i - 1], axis_rows[i]
            if (prev.label or None) != (curr.label or None):
                direction = _direction(prev.label, curr.label)
                transitions.append(TierTransition(
                    axis_name=axis_name,
                    from_label=prev.label,
                    to_label=curr.label,
                    from_p_top=prev.p_top,
                    to_p_top=curr.p_top,
                    scored_at=curr.scored_at,
                    direction=direction,
                ))
                sm = summary_map[axis_name]
                sm["transitions"] += 1
                if curr.escalated:
                    sm["escalations"] += 1
                if direction == "up":
                    sm["regressions"] += 1
                elif direction == "down":
                    sm["improvements"] += 1

    summary = [
        TransitionSummary(
            axis_name=an,
            transition_count=sm["transitions"],
            escalations=sm["escalations"],
            improvements=sm["improvements"],
            regressions=sm["regressions"],
        )
        for an, sm in sorted(summary_map.items())
    ]

    return ServerTierTransitionsResponse(
        server_id=server_id,
        name=srv.name,
        period_days=period_days,
        transitions=transitions,
        summary=summary,
    )


@router.get(
    "/server-score-evolution/{server_id}/velocity",
    response_model=ServerScoreVelocityResponse,
    summary="Get score velocity (p_top delta over time) for a server",
    responses={404: {"description": "Server not found"}},
)
def get_score_velocity(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None, description="Filter to one axis"),
    db: Session = Depends(get_session),
) -> ServerScoreVelocityResponse:
    """Return per-axis p_top delta between consecutive scoring events."""
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    if axis_name is not None and axis_name not in ALL_AXES:
        raise HTTPException(status_code=400, detail=f"Invalid axis. Use one of: {sorted(ALL_AXES)}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    query = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    rows = query.order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.asc()).all()

    by_axis: dict[str, list[McpLlmAxisScore]] = {}
    for r in rows:
        by_axis.setdefault(r.axis_name, []).append(r)

    velocity = []
    for an, axis_rows in sorted(by_axis.items()):
        for i, r in enumerate(axis_rows):
            delta = None
            if i > 0:
                prev_p = axis_rows[i - 1].p_top
                curr_p = r.p_top
                if prev_p is not None and curr_p is not None:
                    delta = round(curr_p - prev_p, 4)
            velocity.append(VelocityPoint(
                date=r.scored_at.strftime("%Y-%m-%d"),
                axis_name=an,
                label=r.label or "UNKNOWN",
                p_top=r.p_top or 0.0,
                delta_p_top=delta,
            ))

    return ServerScoreVelocityResponse(
        server_id=server_id,
        name=srv.name,
        period_days=period_days,
        velocity=velocity,
    )


@router.get(
    "/server-score-evolution/{server_id}/correlations",
    response_model=ServerCorrelationMatrixResponse,
    summary="Get axis correlation matrix for a server",
    responses={404: {"description": "Server not found"}},
)
def get_correlations(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerCorrelationMatrixResponse:
    """Return correlation of label direction between axis pairs over the period."""
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.asc())
        .all()
    )

    # Pivot: scored_at -> {axis_name: label}
    by_time: dict[datetime, dict[str, str]] = {}
    for r in rows:
        by_time.setdefault(r.scored_at, {})[r.axis_name] = r.label or ""

    times = sorted(by_time.keys())
    if len(times) < 2:
        return ServerCorrelationMatrixResponse(
            server_id=server_id, name=srv.name, correlations=[]
        )

    # For each axis pair, check if direction change is aligned
    axis_names = sorted(ALL_AXES)
    correlations: list[AxisCorrelation] = []
    order = ["MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

    def _level(label: str) -> int:
        try:
            return order.index(label.upper())
        except ValueError:
            return -1

    for i, a in enumerate(axis_names):
        for b in axis_names[i + 1:]:
            aligned = 0
            opposing = 0
            total = 0

            for t in range(1, len(times)):
                prev_a = by_time[times[t - 1]].get(a, "")
                curr_a = by_time[times[t]].get(a, "")
                prev_b = by_time[times[t - 1]].get(b, "")
                curr_b = by_time[times[t]].get(b, "")

                da = _level(curr_a) - _level(prev_a)
                db_ = _level(curr_b) - _level(prev_b)

                if da != 0 and db_ != 0:
                    total += 1
                    if da * db_ > 0:
                        aligned += 1
                    else:
                        opposing += 1

            if total == 0:
                corr = "independent"
            elif aligned >= opposing:
                corr = "aligned"
            else:
                corr = "opposing"

            correlations.append(AxisCorrelation(axis_a=a, axis_b=b, correlation=corr))

    return ServerCorrelationMatrixResponse(
        server_id=server_id,
        name=srv.name,
        correlations=correlations,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    d1 = now - timedelta(days=1)
    d2 = now - timedelta(days=2)
    d3 = now - timedelta(days=3)

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="evo-srv-1", name="Evolving Server", risk_tier="HIGH",
            verdict="clean", confidence=0.9,
        ))
        sess.add(McpServerRegistry(
            server_id="evo-srv-2", name="Stable Server", risk_tier="LOW",
            verdict="clean", confidence=0.8,
        ))
        # Day 3 — start of evolution
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-1", axis_name="overall_risk", label="MEDIUM",
            p_top=0.45, p_critical=0.1, p_danger=0.3,
            model_version="v1", adapter_sha256="sha1", decision_rule_version="r1",
            scored_at=d3, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-1", axis_name="auth_strength", label="LOW",
            p_top=0.25, p_critical=0.2, p_danger=0.5,
            model_version="v1", adapter_sha256="sha1", decision_rule_version="r1",
            scored_at=d3, escalated=False,
        ))
        # Day 2 — escalation
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-1", axis_name="overall_risk", label="HIGH",
            p_top=0.70, p_critical=0.1, p_danger=0.2,
            model_version="v1", adapter_sha256="sha2", decision_rule_version="r1",
            scored_at=d2, escalated=True,
        ))
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-1", axis_name="auth_strength", label="MEDIUM",
            p_top=0.50, p_critical=0.2, p_danger=0.3,
            model_version="v1", adapter_sha256="sha2", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        # Day 1 — peak
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-1", axis_name="overall_risk", label="CRITICAL",
            p_top=0.88, p_critical=0.05, p_danger=0.07,
            model_version="v1", adapter_sha256="sha3", decision_rule_version="r1",
            scored_at=d1, escalated=True,
        ))
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-1", axis_name="auth_strength", label="HIGH",
            p_top=0.75, p_critical=0.1, p_danger=0.15,
            model_version="v1", adapter_sha256="sha3", decision_rule_version="r1",
            scored_at=d1, escalated=False,
        ))
        # evo-srv-2 — single score (no evolution possible)
        sess.add(McpLlmAxisScore(
            server_id="evo-srv-2", axis_name="overall_risk", label="LOW",
            p_top=0.20, p_critical=0.05, p_danger=0.10,
            model_version="v1", adapter_sha256="sha4", decision_rule_version="r1",
            scored_at=d1, escalated=False,
        ))
        sess.commit()

    client = TestClient(app)

    # ---- Test 1: evolution endpoint ----
    resp = client.get("/api/server-score-evolution/evo-srv-1", params={"period_days": 7})
    if resp.status_code != 200:
        print(f"FAIL: evolution 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "evo-srv-1":
        print(f"FAIL: wrong server_id {data['server_id']}", file=sys.stderr)
        sys.exit(1)
    if data["name"] != "Evolving Server":
        print(f"FAIL: wrong name {data['name']}", file=sys.stderr)
        sys.exit(1)
    if data["total_score_events"] != 6:
        print(f"FAIL: expected 6 events, got {data['total_score_events']}", file=sys.stderr)
        sys.exit(1)
    # Check overall_risk trajectory
    overall_traj = next((t for t in data["trajectories"] if t["axis_name"] == "overall_risk"), None)
    if overall_traj is None:
        print("FAIL: no overall_risk trajectory", file=sys.stderr)
        sys.exit(1)
    if overall_traj["first_label"] != "MEDIUM":
        print(f"FAIL: first_label expected MEDIUM, got {overall_traj['first_label']}", file=sys.stderr)
        sys.exit(1)
    if overall_traj["last_label"] != "CRITICAL":
        print(f"FAIL: last_label expected CRITICAL, got {overall_traj['last_label']}", file=sys.stderr)
        sys.exit(1)
    if not overall_traj["label_changed"]:
        print("FAIL: label_changed should be True", file=sys.stderr)
        sys.exit(1)

    # ---- Test 2: transitions endpoint ----
    resp2 = client.get("/api/server-score-evolution/evo-srv-1/transitions", params={"period_days": 7})
    if resp2.status_code != 200:
        print(f"FAIL: transitions 200, got {resp2.status_code}: {resp2.text}", file=sys.stderr)
        sys.exit(1)
    trans = resp2.json()
    if not trans["transitions"]:
        print("FAIL: expected transitions", file=sys.stderr)
        sys.exit(1)
    # Overall risk should have 2 transitions: MEDIUM→HIGH, HIGH→CRITICAL
    overall_trans = [t for t in trans["transitions"] if t["axis_name"] == "overall_risk"]
    if len(overall_trans) != 2:
        print(f"FAIL: expected 2 overall_risk transitions, got {len(overall_trans)}", file=sys.stderr)
        sys.exit(1)
    if overall_trans[0]["direction"] != "up":
        print(f"FAIL: first direction expected up, got {overall_trans[0]['direction']}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 3: velocity endpoint ----
    resp3 = client.get("/api/server-score-evolution/evo-srv-1/velocity", params={"period_days": 7})
    if resp3.status_code != 200:
        print(f"FAIL: velocity 200, got {resp3.status_code}: {resp3.text}", file=sys.stderr)
        sys.exit(1)
    vel = resp3.json()
    if len(vel["velocity"]) != 6:
        print(f"FAIL: expected 6 velocity points, got {len(vel['velocity'])}", file=sys.stderr)
        sys.exit(1)
    # Check p_top delta is present for consecutive rows of same axis
    overall_vel = [v for v in vel["velocity"] if v["axis_name"] == "overall_risk"]
    if len(overall_vel) != 3:
        print(f"FAIL: expected 3 overall_risk velocity points, got {len(overall_vel)}", file=sys.stderr)
        sys.exit(1)
    if overall_vel[0]["delta_p_top"] is not None:
        print(f"FAIL: first delta should be None, got {overall_vel[0]['delta_p_top']}", file=sys.stderr)
        sys.exit(1)
    if overall_vel[1]["delta_p_top"] is None:
        print("FAIL: second delta should not be None", file=sys.stderr)
        sys.exit(1)

    # ---- Test 4: correlations endpoint ----
    resp4 = client.get("/api/server-score-evolution/evo-srv-1/correlations", params={"period_days": 7})
    if resp4.status_code != 200:
        print(f"FAIL: correlations 200, got {resp4.status_code}: {resp4.text}", file=sys.stderr)
        sys.exit(1)
    corr = resp4.json()
    if not corr["correlations"]:
        print("FAIL: expected correlations", file=sys.stderr)
        sys.exit(1)

    # ---- Test 5: 404 ----
    resp5 = client.get("/api/server-score-evolution/no-such-server", params={"period_days": 7})
    if resp5.status_code != 404:
        print(f"FAIL: expected 404, got {resp5.status_code}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 6: stable server (no transitions) ----
    resp6 = client.get("/api/server-score-evolution/evo-srv-2/transitions", params={"period_days": 7})
    if resp6.status_code != 200:
        print(f"FAIL: stable server transitions 200, got {resp6.status_code}", file=sys.stderr)
        sys.exit(1)
    if resp6.json()["transitions"]:
        print("FAIL: stable server should have no transitions", file=sys.stderr)
        sys.exit(1)

    # ---- Test 7: invalid axis in velocity ----
    resp7 = client.get("/api/server-score-evolution/evo-srv-1/velocity", params={"axis_name": "not_an_axis"})
    if resp7.status_code != 400:
        print(f"FAIL: expected 400 for invalid axis, got {resp7.status_code}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 8: days validation ----
    resp8 = client.get("/api/server-score-evolution/evo-srv-1", params={"period_days": 0})
    if resp8.status_code != 422:
        print(f"FAIL: expected 422 for period_days=0, got {resp8.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
