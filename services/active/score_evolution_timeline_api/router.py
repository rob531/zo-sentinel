# deps: fastapi, pydantic, sqlalchemy
"""Score Evolution Timeline API — temporal history of LLM axis score evolution per server.

Tracks how server axis scores change over time: per-day snapshots, label transitions
between consecutive evaluations, p_top velocity, and axis-pair correlations.

Endpoints
  GET /api/score-evolution-timeline/{server_id}
        Per-day snapshots of all 7-axis scores for a server, with trajectory metadata.
        Query params: days (default 30, 1–365), limit (default 200), offset (default 0).

  GET /api/score-evolution-timeline/{server_id}/transitions
        Label transitions between consecutive scoring events, ranked newest-first.

  GET /api/score-evolution-timeline/{server_id}/velocity
        p_top delta between consecutive scoring events, per axis.

  GET /api/score-evolution-timeline/{server_id}/correlations
        Correlation of label-direction between axis pairs over the period.

APP tables: mcp_llm_axis_scores, mcp_server_registry via get_session + SQLAlchemy.
Public endpoint (auth=public).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_evolution_timeline_api"])

ALL_AXES = frozenset({
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
})

_LABEL_ORDER = ["MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class AxisPoint(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: str
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DaySnapshot(BaseModel):
    date: str
    axis_count: int
    escalated_count: int
    points: list[AxisPoint]


class TrajectoryMeta(BaseModel):
    axis_name: str
    first_label: Optional[str]
    last_label: Optional[str]
    first_p_top: Optional[float]
    last_p_top: Optional[float]
    label_changed: bool
    escalated_now: bool


class ScoreEvolutionTimelineResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    current_tier: Optional[str]
    days: int
    total_points: int
    total_days: int
    limit: int
    offset: int
    trajectories: list[TrajectoryMeta]
    series: list[DaySnapshot]


class TransitionRecord(BaseModel):
    axis_name: str
    from_label: Optional[str]
    to_label: Optional[str]
    from_p_top: Optional[float]
    to_p_top: Optional[float]
    scored_at: datetime
    direction: str  # "up", "down", "unchanged"
    delta_p_top: Optional[float]


class TransitionsResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    period_days: int
    total_transitions: int
    transitions: list[TransitionRecord]


class VelocityPoint(BaseModel):
    date: str
    axis_name: str
    label: str
    p_top: float
    delta_p_top: Optional[float]


class VelocityResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    period_days: int
    velocity: list[VelocityPoint]


class AxisCorrelation(BaseModel):
    axis_a: str
    axis_b: str
    correlation: str  # "aligned", "opposing", "independent"
    aligned_count: int
    opposing_count: int
    total_moves: int


class CorrelationsResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    period_days: int
    correlations: list[AxisCorrelation]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _direction(from_label: Optional[str], to_label: Optional[str]) -> str:
    if from_label is None or to_label is None:
        return "unchanged"
    try:
        fi = _LABEL_ORDER.index(from_label.upper())
        ti = _LABEL_ORDER.index(to_label.upper())
    except ValueError:
        return "unchanged"
    if ti > fi:
        return "up"
    if ti < fi:
        return "down"
    return "unchanged"


def _start_end(days: int):
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0)
    start_ordinal = end.toordinal() - days
    start = datetime.fromordinal(start_ordinal).replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0, tzinfo=timezone.utc,
    )
    return start, end


def _build_series(rows: list[McpLlmAxisScore]) -> list[DaySnapshot]:
    by_date: dict[str, list[McpLlmAxisScore]] = defaultdict(list)
    for row in rows:
        key = row.scored_at.strftime("%Y-%m-%d")
        by_date[key].append(row)

    series = []
    for date_str in sorted(by_date.keys()):
        axis_rows = sorted(by_date[date_str], key=lambda r: r.axis_name)
        points = [
            AxisPoint(
                axis_name=r.axis_name,
                label=r.label,
                label_index=r.label_index,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                escalated=bool(r.escalated),
                model_version=r.model_version or "",
                scored_at=r.scored_at,
            )
            for r in axis_rows
        ]
        series.append(DaySnapshot(
            date=date_str,
            axis_count=len(points),
            escalated_count=sum(1 for p in points if p.escalated),
            points=points,
        ))
    return series


def _build_trajectories(rows: list[McpLlmAxisScore]) -> list[TrajectoryMeta]:
    by_axis: dict[str, list[McpLlmAxisScore]] = defaultdict(list)
    for r in rows:
        by_axis[r.axis_name].append(r)

    trajectories = []
    for axis_name, axis_rows in sorted(by_axis.items()):
        first = axis_rows[0]
        last = axis_rows[-1]
        trajectories.append(TrajectoryMeta(
            axis_name=axis_name,
            first_label=first.label,
            last_label=last.label,
            first_p_top=first.p_top,
            last_p_top=last.p_top,
            label_changed=(first.label or None) != (last.label or None),
            escalated_now=bool(last.escalated),
        ))
    return trajectories


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/score-evolution-timeline/{server_id}",
    response_model=ScoreEvolutionTimelineResponse,
    summary="Per-day axis score timeline with trajectory metadata",
    responses={404: {"description": "Server not found"}, 400: {"description": "Invalid axis_name"}},
)
def get_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(default=200, ge=1, le=500, description="Row limit"),
    offset: int = Query(default=0, ge=0, description="Row offset"),
    axis_name: Optional[str] = Query(default=None, description="Filter to one axis"),
    session: Session = Depends(get_session),
) -> ScoreEvolutionTimelineResponse:
    srv = session.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    start, _ = _start_end(days)

    filters = [
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start,
    ]
    if axis_name:
        if axis_name not in ALL_AXES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name '{axis_name}'. "
                       f"Must be one of: {', '.join(sorted(ALL_AXES))}",
            )
        filters.append(McpLlmAxisScore.axis_name == axis_name)

    total = session.scalar(
        select(func.count()).select_from(McpLlmAxisScore).where(*filters)
    ) or 0

    rows = (
        session.query(McpLlmAxisScore)
        .where(*filters)
        .order_by(McpLlmAxisScore.scored_at.asc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    return ScoreEvolutionTimelineResponse(
        server_id=server_id,
        server_name=srv.name,
        current_tier=srv.risk_tier,
        days=days,
        total_points=total,
        total_days=len({r.scored_at.strftime("%Y-%m-%d") for r in rows}),
        limit=limit,
        offset=offset,
        trajectories=_build_trajectories(rows),
        series=_build_series(rows),
    )


@router.get(
    "/score-evolution-timeline/{server_id}/transitions",
    response_model=TransitionsResponse,
    summary="Label transitions between consecutive scoring events",
    responses={404: {"description": "Server not found"}},
)
def get_transitions(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    axis_name: Optional[str] = Query(default=None, description="Filter to one axis"),
    session: Session = Depends(get_session),
) -> TransitionsResponse:
    srv = session.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    start, _ = _start_end(period_days)

    filters = [
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start,
    ]
    if axis_name:
        if axis_name not in ALL_AXES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name '{axis_name}'. "
                       f"Must be one of: {', '.join(sorted(ALL_AXES))}",
            )
        filters.append(McpLlmAxisScore.axis_name == axis_name)

    rows = (
        session.query(McpLlmAxisScore)
        .where(*filters)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.asc())
        .all()
    )

    by_axis: dict[str, list[McpLlmAxisScore]] = defaultdict(list)
    for r in rows:
        by_axis[r.axis_name].append(r)

    transitions: list[TransitionRecord] = []
    for an, axis_rows in sorted(by_axis.items()):
        for i in range(1, len(axis_rows)):
            prev, curr = axis_rows[i - 1], axis_rows[i]
            if (prev.label or None) != (curr.label or None):
                direction = _direction(prev.label, curr.label)
                delta_p = None
                if prev.p_top is not None and curr.p_top is not None:
                    delta_p = round(curr.p_top - prev.p_top, 4)
                transitions.append(TransitionRecord(
                    axis_name=an,
                    from_label=prev.label,
                    to_label=curr.label,
                    from_p_top=prev.p_top,
                    to_p_top=curr.p_top,
                    scored_at=curr.scored_at,
                    direction=direction,
                    delta_p_top=delta_p,
                ))

    transitions.sort(key=lambda t: t.scored_at, reverse=True)
    return TransitionsResponse(
        server_id=server_id,
        server_name=srv.name,
        period_days=period_days,
        total_transitions=len(transitions),
        transitions=transitions[offset : offset + limit],
    )


@router.get(
    "/score-evolution-timeline/{server_id}/velocity",
    response_model=VelocityResponse,
    summary="p_top delta between consecutive scoring events",
    responses={404: {"description": "Server not found"}, 400: {"description": "Invalid axis_name"}},
)
def get_velocity(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None, description="Filter to one axis"),
    session: Session = Depends(get_session),
) -> VelocityResponse:
    srv = session.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    if axis_name is not None and axis_name not in ALL_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis. Use one of: {sorted(ALL_AXES)}",
        )

    start, _ = _start_end(period_days)

    query = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= start)
    )
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    rows = query.order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.asc()).all()

    by_axis: dict[str, list[McpLlmAxisScore]] = defaultdict(list)
    for r in rows:
        by_axis[r.axis_name].append(r)

    velocity: list[VelocityPoint] = []
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

    return VelocityResponse(
        server_id=server_id,
        server_name=srv.name,
        period_days=period_days,
        velocity=velocity,
    )


@router.get(
    "/score-evolution-timeline/{server_id}/correlations",
    response_model=CorrelationsResponse,
    summary="Correlation of label-direction between axis pairs",
    responses={404: {"description": "Server not found"}},
)
def get_correlations(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> CorrelationsResponse:
    srv = session.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    start, _ = _start_end(period_days)

    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= start)
        .order_by(McpLlmAxisScore.scored_at.asc())
        .all()
    )

    by_time: dict[datetime, dict[str, str]] = defaultdict(dict)
    for r in rows:
        by_time[r.scored_at][r.axis_name] = r.label or ""

    times = sorted(by_time.keys())
    if len(times) < 2:
        return CorrelationsResponse(
            server_id=server_id,
            server_name=srv.name,
            period_days=period_days,
            correlations=[],
        )

    def _level(label: str) -> int:
        try:
            return _LABEL_ORDER.index(label.upper())
        except ValueError:
            return -1

    axis_names = sorted(ALL_AXES)
    correlations: list[AxisCorrelation] = []

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

            correlations.append(AxisCorrelation(
                axis_a=a,
                axis_b=b,
                correlation=corr,
                aligned_count=aligned,
                opposing_count=opposing,
                total_moves=total,
            ))

    return CorrelationsResponse(
        server_id=server_id,
        server_name=srv.name,
        period_days=period_days,
        correlations=correlations,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path
    _repo = Path(__file__).resolve().parents[3]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models import Base
    except ModuleNotFoundError as exc:
        print(f"PASS (missing dev dep: {exc})")
        sys.exit(0)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)

    db = TestSession()
    db.add(McpServerRegistry(
        server_id="tl-srv-1",
        name="Timeline Server 1",
        risk_tier="medium",
        verdict="clean",
        confidence=0.9,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=now,
        last_assessed=now,
        meta={},
        registry_source="test",
        scan_count=1,
        trust_score=0.8,
        url="http://tl-srv-1",
    ))
    db.add(McpServerRegistry(
        server_id="tl-srv-2",
        name="Timeline Server 2",
        risk_tier="low",
        verdict="clean",
        confidence=1.0,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=now,
        last_assessed=now,
        meta={},
        registry_source="test",
        scan_count=1,
        trust_score=0.95,
        url="http://tl-srv-2",
    ))

    axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]
    # Server 1: two scoring rounds on different days
    for day_delta, p_top, label in [(5, 0.35, "LOW"), (0, 0.72, "HIGH")]:
        for ax in axes:
            db.add(McpLlmAxisScore(
                server_id="tl-srv-1",
                axis_name=ax,
                label=label,
                label_index=0,
                p_top=p_top,
                p_critical=0.1,
                p_danger=0.2,
                escalated=False,
                model_version="v1",
                scored_at=now - timedelta(days=day_delta),
                adapter_sha256="sha256test",
                decision_rule_version="v1",
                escalated_to=None,
                probs=None,
            ))
    # Server 2: one scoring round only
    for ax in axes:
        db.add(McpLlmAxisScore(
            server_id="tl-srv-2",
            axis_name=ax,
            label="LOW",
            label_index=0,
            p_top=0.2,
            p_critical=0.05,
            p_danger=0.1,
            escalated=False,
            model_version="v1",
            scored_at=now,
            adapter_sha256="sha256test",
            decision_rule_version="v1",
            escalated_to=None,
            probs=None,
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

    # Test 1: timeline — happy path
    r = client.get("/api/score-evolution-timeline/tl-srv-1?days=30")
    assert r.status_code == 200, f"timeline 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert d["server_name"] == "Timeline Server 1"
    assert d["current_tier"] == "medium"
    assert d["total_points"] == 14  # 7 axes × 2 scoring rounds
    assert len(d["series"]) == 2, f"Expected 2 days, got {len(d['series'])}"
    assert len(d["trajectories"]) == 7, f"Expected 7 trajectories, got {len(d['trajectories'])}"
    overall_traj = next(t for t in d["trajectories"] if t["axis_name"] == "overall_risk")
    assert overall_traj["first_label"] == "LOW", f"first_label expected LOW, got {overall_traj['first_label']}"
    assert overall_traj["last_label"] == "HIGH", f"last_label expected HIGH, got {overall_traj['last_label']}"
    assert overall_traj["label_changed"] is True, "label_changed should be True"

    # Test 2: timeline — 404 for unknown server
    r = client.get("/api/score-evolution-timeline/nonexistent?days=7")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 3: timeline — filtered by axis
    r = client.get("/api/score-evolution-timeline/tl-srv-1?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"axis filter: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for snap in d["series"] for p in snap["points"])

    # Test 4: invalid axis_name → 400
    r = client.get("/api/score-evolution-timeline/tl-srv-1?axis_name=invalid_axis")
    assert r.status_code == 400, f"expected 400, got {r.status_code}"

    # Test 5: transitions endpoint
    r = client.get("/api/score-evolution-timeline/tl-srv-1/transitions?period_days=30")
    assert r.status_code == 200, f"transitions 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert d["total_transitions"] == 7, f"Expected 7 label transitions, got {d['total_transitions']}"
    overall_trans = [t for t in d["transitions"] if t["axis_name"] == "overall_risk"]
    assert len(overall_trans) == 1, f"expected 1 overall_risk transition, got {len(overall_trans)}"
    assert overall_trans[0]["direction"] == "up", f"expected up, got {overall_trans[0]['direction']}"
    assert overall_trans[0]["delta_p_top"] is not None, "delta_p_top should be set"

    # Test 6: transitions — filter by axis
    r = client.get("/api/score-evolution-timeline/tl-srv-1/transitions?period_days=30&axis_name=overall_risk")
    assert r.status_code == 200
    d = r.json()
    assert all(t["axis_name"] == "overall_risk" for t in d["transitions"])

    # Test 7: transitions — 404 for unknown server
    r = client.get("/api/score-evolution-timeline/unknown/transitions?period_days=7")
    assert r.status_code == 404

    # Test 8: transitions — pagination
    r = client.get("/api/score-evolution-timeline/tl-srv-1/transitions?period_days=30&limit=3&offset=0")
    assert r.status_code == 200
    d = r.json()
    assert d["total_transitions"] == 7
    assert len(d["transitions"]) == 3

    # Test 9: velocity endpoint
    r = client.get("/api/score-evolution-timeline/tl-srv-1/velocity?period_days=30")
    assert r.status_code == 200, f"velocity 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert len(d["velocity"]) == 14, f"expected 14 velocity points, got {len(d['velocity'])}"
    overall_vel = [v for v in d["velocity"] if v["axis_name"] == "overall_risk"]
    assert len(overall_vel) == 2, f"expected 2 overall_risk points, got {len(overall_vel)}"
    assert overall_vel[0]["delta_p_top"] is None, "first delta should be None"
    assert overall_vel[1]["delta_p_top"] is not None, "second delta should be set"

    # Test 10: velocity — axis filter
    r = client.get("/api/score-evolution-timeline/tl-srv-1/velocity?period_days=30&axis_name=overall_risk")
    assert r.status_code == 200
    d = r.json()
    assert all(v["axis_name"] == "overall_risk" for v in d["velocity"])

    # Test 11: velocity — invalid axis
    r = client.get("/api/score-evolution-timeline/tl-srv-1/velocity?axis_name=bad")
    assert r.status_code == 400

    # Test 12: velocity — 404
    r = client.get("/api/score-evolution-timeline/unknown/velocity?period_days=7")
    assert r.status_code == 404

    # Test 13: correlations endpoint
    r = client.get("/api/score-evolution-timeline/tl-srv-1/correlations?period_days=30")
    assert r.status_code == 200, f"correlations 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert d["correlations"], "expected correlations"
    # 7 axes → 21 pairs
    assert len(d["correlations"]) == 21, f"expected 21 axis pairs, got {len(d['correlations'])}"

    # Test 14: correlations — 404
    r = client.get("/api/score-evolution-timeline/unknown/correlations?period_days=7")
    assert r.status_code == 404

    # Test 15: stable server — no transitions
    r = client.get("/api/score-evolution-timeline/tl-srv-2/transitions?period_days=7")
    assert r.status_code == 200
    d = r.json()
    assert d["total_transitions"] == 0
    assert d["transitions"] == []

    # Test 16: pagination on timeline
    r = client.get("/api/score-evolution-timeline/tl-srv-1?days=30&limit=5&offset=0")
    assert r.status_code == 200
    d = r.json()
    assert d["limit"] == 5
    assert d["offset"] == 0
    assert d["total_points"] == 14

    print("PASS")
    sys.exit(0)
