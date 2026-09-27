# deps: fastapi, pydantic, sqlalchemy
"""server_axis_timeline_api -- date-grouped LLM axis score history per server.

Endpoints
  GET /api/servers/{server_id}/axis-timeline
        Per-day snapshots of all 7-axis scores for a server.
        Query params: days (default 30, 1–365), limit (default 200), offset (default 0).

  GET /api/servers/{server_id}/axis-timeline/{axis_name}
        Filtered to a single axis, same response shape.

  GET /api/servers/{server_id}/axis-timeline-latest
        Latest 7-axis verdict (one row per axis, most recent scoring event).

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

router = APIRouter(prefix="/api", tags=["server_axis_timeline_api"])

AXIS_NAMES = frozenset(
    "overall_risk auth_strength capability_breadth data_sensitivity "
    "network_egress maintainer_trust exploit_surface".split()
)


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class AxisPoint(BaseModel):
    """Single axis probability snapshot at one scoring event."""
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
    """One calendar date's worth of axis scores for a server."""
    date: str
    points: list[AxisPoint]


class AxisTimelineResponse(BaseModel):
    """Per-day axis score history for a server."""
    server_id: str
    server_name: Optional[str]
    current_tier: Optional[str]
    days: int
    total_points: int
    limit: int
    offset: int
    series: list[DaySnapshot]


class AxisVerdictEntry(BaseModel):
    """One axis at its most recent scoring event."""
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    escalated_to: Optional[str]
    model_version: str
    scored_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AxisLatestResponse(BaseModel):
    """Latest 7-axis verdict for a server."""
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    axes: list[AxisVerdictEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _start_end(days: int):
    """Return (start, end) UTC datetimes for a lookback window."""
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0)
    start_ordinal = end.toordinal() - days
    start = datetime.fromordinal(start_ordinal).replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0, tzinfo=timezone.utc,
    )
    return start, end


def _build_series(rows: list[McpLlmAxisScore]) -> list[DaySnapshot]:
    """Group raw axis-score rows into per-date DaySnapshot objects."""
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
        series.append(DaySnapshot(date=date_str, points=points))
    return series


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/servers/{server_id}/axis-timeline",
    response_model=AxisTimelineResponse,
    summary="Per-day axis score timeline for a server",
    responses={404: {"description": "Server not found"}, 400: {"description": "Invalid axis_name"}},
)
def get_axis_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(default=200, ge=1, le=500, description="Row limit"),
    offset: int = Query(default=0, ge=0, description="Row offset"),
    axis_name: Optional[str] = Query(default=None, description="Filter to one axis"),
    session: Session = Depends(get_session),
) -> AxisTimelineResponse:
    """Return date-grouped axis score history for a server."""
    srv = session.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    start, _ = _start_end(days)

    filters = [
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start,
    ]
    if axis_name:
        if axis_name not in AXIS_NAMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name '{axis_name}'. "
                       f"Must be one of: {', '.join(sorted(AXIS_NAMES))}",
            )
        filters.append(McpLlmAxisScore.axis_name == axis_name)

    total = session.scalar(
        select(func.count()).select_from(McpLlmAxisScore).where(*filters)
    ) or 0

    rows = (
        session.query(McpLlmAxisScore)
        .where(*filters)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    return AxisTimelineResponse(
        server_id=server_id,
        server_name=srv.name,
        current_tier=srv.risk_tier,
        days=days,
        total_points=total,
        limit=limit,
        offset=offset,
        series=_build_series(rows),
    )


@router.get(
    "/servers/{server_id}/axis-timeline/{axis_name}",
    response_model=AxisTimelineResponse,
    summary="Per-day axis score timeline for a specific axis",
    responses={404: {"description": "Server not found"}, 400: {"description": "Invalid axis_name"}},
)
def get_single_axis_timeline(
    server_id: str,
    axis_name: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> AxisTimelineResponse:
    """Return date-grouped axis score history for one specific axis."""
    if axis_name not in AXIS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name '{axis_name}'. "
                   f"Must be one of: {', '.join(sorted(AXIS_NAMES))}",
        )
    return get_axis_timeline(
        server_id=server_id,
        days=days,
        limit=limit,
        offset=offset,
        axis_name=axis_name,
        session=session,
    )


@router.get(
    "/servers/{server_id}/axis-timeline-latest",
    response_model=AxisLatestResponse,
    summary="Latest 7-axis verdict for a server",
    responses={404: {"description": "Server not found"}},
)
def get_axis_timeline_latest(
    server_id: str,
    session: Session = Depends(get_session),
) -> AxisLatestResponse:
    """Return the most recent axis-score row for each of the 7 axes."""
    srv = session.get(McpServerRegistry, server_id)
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    rows = (
        session.query(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.max_scored_at),
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    axes = [
        AxisVerdictEntry(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            escalated_to=r.escalated_to,
            model_version=r.model_version or "",
            scored_at=r.scored_at,
        )
        for r in rows
    ]

    return AxisLatestResponse(
        server_id=server_id,
        server_name=srv.name,
        risk_tier=srv.risk_tier,
        axes=axes,
    )


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

    # Seed data
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
    # Server 2: one scoring round
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
    r = client.get("/api/servers/tl-srv-1/axis-timeline?days=30")
    assert r.status_code == 200, f"timeline 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert d["server_name"] == "Timeline Server 1"
    assert d["current_tier"] == "medium"
    assert d["total_points"] == 14  # 7 axes × 2 scoring rounds
    assert len(d["series"]) == 2, f"Expected 2 days, got {len(d['series'])}"

    # Test 2: timeline — 404 for unknown server
    r = client.get("/api/servers/nonexistent/axis-timeline?days=7")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 3: timeline — filtered by axis
    r = client.get("/api/servers/tl-srv-1/axis-timeline?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"axis filter: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for snap in d["series"] for p in snap["points"])

    # Test 4: single-axis endpoint
    r = client.get("/api/servers/tl-srv-1/axis-timeline/overall_risk?days=30")
    assert r.status_code == 200, f"single axis: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for snap in d["series"] for p in snap["points"])

    # Test 5: invalid axis_name → 400
    r = client.get("/api/servers/tl-srv-1/axis-timeline?axis_name=invalid_axis")
    assert r.status_code == 400, f"expected 400, got {r.status_code}"

    # Test 6: axis-latest endpoint
    r = client.get("/api/servers/tl-srv-1/axis-timeline-latest")
    assert r.status_code == 200, f"latest 200: {r.text}"
    d = r.json()
    assert len(d["axes"]) == 7, f"expected 7 axes, got {len(d['axes'])}"
    axis_names = {a["axis_name"] for a in d["axes"]}
    for ax in axes:
        assert ax in axis_names, f"missing {ax}"
    # Most recent round should have label=HIGH (p_top=0.72)
    overall = next(a for a in d["axes"] if a["axis_name"] == "overall_risk")
    assert overall["label"] == "HIGH", f"Expected HIGH, got {overall['label']}"

    # Test 7: axis-latest 404
    r = client.get("/api/servers/unknown/axis-timeline-latest")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 8: pagination
    r = client.get("/api/servers/tl-srv-1/axis-timeline?days=30&limit=5&offset=0")
    assert r.status_code == 200
    d = r.json()
    assert d["total_points"] == 14
    # Series length is by-day (not per-row), so still 2 days
    assert d["limit"] == 5
    assert d["offset"] == 0

    print("PASS")
    sys.exit(0)
