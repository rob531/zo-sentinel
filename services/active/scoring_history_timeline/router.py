# deps: fastapi, pydantic, sqlalchemy
"""Scoring History Timeline — per-server scoring history timeline and distribution.

Endpoints:
  GET /api/scoring/timeline/history/{server_id}  -- per-server axis score timeline
  GET /api/scoring/timeline/distribution          -- axis label distribution across servers

Public endpoint (auth=public).  Reads from mcp_llm_axis_scores via app.db.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so `app.db` resolves when run directly
_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scoring/timeline", tags=["scoring_history_timeline"])


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class TimelineAxisEntry(BaseModel):
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


class ServerTimelineSnapshot(BaseModel):
    date: str
    axes: list[TimelineAxisEntry]


class ServerTimelineResponse(BaseModel):
    server_id: str
    name: Optional[str]
    days: int
    snapshots: list[ServerTimelineSnapshot]


class AxisDistributionBucket(BaseModel):
    label: str
    count: int


class AxisDistributionResponse(BaseModel):
    as_of: str
    axis_name: str
    total_servers: int
    buckets: list[AxisDistributionBucket]


class TimelineDistributionResponse(BaseModel):
    as_of: str
    axes: list[AxisDistributionResponse]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_AXES = frozenset({
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
})


def _latest_snapshot_per_day(
    rows: list[McpLlmAxisScore],
) -> list[ServerTimelineSnapshot]:
    """Group rows by date; for each date pick the most-recent scored_at row per axis."""
    by_date: dict[str, dict[str, McpLlmAxisScore]] = {}
    for row in rows:
        key = row.scored_at.strftime("%Y-%m-%d")
        by_date.setdefault(key, {})
        existing = by_date[key].get(row.axis_name)
        if existing is None or row.scored_at > existing.scored_at:
            by_date[key][row.axis_name] = row

    snapshots = []
    for date, axis_map in sorted(by_date.items(), reverse=True):
        axes = [
            TimelineAxisEntry(
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
            for r in sorted(axis_map.values(), key=lambda x: x.axis_name)
        ]
        snapshots.append(ServerTimelineSnapshot(date=date, axes=axes))
    return snapshots


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/history/{server_id}",
    response_model=ServerTimelineResponse,
    summary="Get scoring history timeline for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerTimelineResponse:
    """
    Returns a day-by-day axis score timeline for the given server over the
    requested period.  Each snapshot shows the latest axis scores for that date.
    """
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    return ServerTimelineResponse(
        server_id=server_id,
        name=srv.name,
        days=days,
        snapshots=_latest_snapshot_per_day(rows),
    )


@router.get(
    "/distribution",
    response_model=TimelineDistributionResponse,
    summary="Get axis label distribution across servers",
)
def get_timeline_distribution(
    days: int = Query(default=30, ge=1, le=365),
    axis: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> TimelineDistributionResponse:
    """
    Returns the label distribution per axis across all servers scored in the
    past *days*.  Optionally filter to a single axis.
    """
    if axis is not None and axis not in ALL_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis. Must be one of: {sorted(ALL_AXES)}",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    axes_to_query = [axis] if axis else sorted(ALL_AXES)
    result_axes: list[AxisDistributionResponse] = []

    for ax in axes_to_query:
        total_servers = db.execute(
            select(func.count(func.distinct(McpLlmAxisScore.server_id)))
            .where(McpLlmAxisScore.axis_name == ax)
            .where(McpLlmAxisScore.scored_at >= cutoff)
        ).scalar_one_or_none()

        if total_servers and total_servers > 0:
            bucket_rows = db.execute(
                select(
                    McpLlmAxisScore.label,
                    func.count(func.distinct(McpLlmAxisScore.server_id)).label("count"),
                )
                .where(McpLlmAxisScore.axis_name == ax)
                .where(McpLlmAxisScore.scored_at >= cutoff)
                .group_by(McpLlmAxisScore.label)
                .order_by(McpLlmAxisScore.label)
            ).all()
        else:
            bucket_rows = []

        result_axes.append(AxisDistributionResponse(
            as_of=datetime.now(timezone.utc).isoformat(),
            axis_name=ax,
            total_servers=total_servers or 0,
            buckets=[
                AxisDistributionBucket(label=row.label or "UNKNOWN", count=row.count)
                for row in bucket_rows
            ],
        ))

    return TimelineDistributionResponse(
        as_of=datetime.now(timezone.utc).isoformat(),
        axes=result_axes,
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
    from app.db import get_session as _real_get_session

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
    app.dependency_overrides[_real_get_session] = _override

    now = datetime.now(timezone.utc)
    d1 = now - timedelta(days=1)
    d2 = now - timedelta(days=2)
    d3 = now - timedelta(days=3)

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="srv1", name="Test Server", risk_tier="HIGH",
            verdict="clean", confidence=0.9,
        ))
        sess.add(McpServerRegistry(
            server_id="srv2", name="Other Server", risk_tier="LOW",
            verdict="clean", confidence=0.8,
        ))
        # Day 3 scores (oldest)
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk", label="HIGH",
            p_top=0.7, p_critical=0.1, p_danger=0.2,
            model_version="v1", adapter_sha256="sha1", decision_rule_version="r1",
            scored_at=d3, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="auth_strength", label="MEDIUM",
            p_top=0.5, p_critical=0.2, p_danger=0.3,
            model_version="v1", adapter_sha256="sha1", decision_rule_version="r1",
            scored_at=d3, escalated=False,
        ))
        # Day 2 scores
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk", label="HIGH",
            p_top=0.75, p_critical=0.1, p_danger=0.15,
            model_version="v1", adapter_sha256="sha2", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="auth_strength", label="LOW",
            p_top=0.35, p_critical=0.25, p_danger=0.4,
            model_version="v1", adapter_sha256="sha2", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        # Day 1 scores (latest)
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk", label="CRITICAL",
            p_top=0.88, p_critical=0.05, p_danger=0.07,
            model_version="v1", adapter_sha256="sha3", decision_rule_version="r1",
            scored_at=d1, escalated=True,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="auth_strength", label="LOW",
            p_top=0.3, p_critical=0.3, p_danger=0.4,
            model_version="v1", adapter_sha256="sha3", decision_rule_version="r1",
            scored_at=d1, escalated=False,
        ))
        # srv2 has scores too
        sess.add(McpLlmAxisScore(
            server_id="srv2", axis_name="overall_risk", label="LOW",
            p_top=0.2, p_critical=0.05, p_danger=0.1,
            model_version="v1", adapter_sha256="sha4", decision_rule_version="r1",
            scored_at=d1, escalated=False,
        ))
        sess.commit()

    client = TestClient(app)

    # ---- Test 1: timeline for srv1 ----
    resp = client.get("/api/scoring/timeline/history/srv1", params={"days": 7})
    if resp.status_code != 200:
        print(f"FAIL: timeline 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv1":
        print(f"FAIL: wrong server_id {data['server_id']}", file=sys.stderr)
        sys.exit(1)
    if data["name"] != "Test Server":
        print(f"FAIL: wrong name {data['name']}", file=sys.stderr)
        sys.exit(1)
    # Should have 3 snapshot dates
    if len(data["snapshots"]) != 3:
        print(f"FAIL: expected 3 snapshots, got {len(data['snapshots'])}", file=sys.stderr)
        sys.exit(1)
    # Latest snapshot should have CRITICAL overall_risk
    latest = data["snapshots"][0]
    if latest["date"] != d1.strftime("%Y-%m-%d"):
        print(f"FAIL: latest date expected {d1.date()}, got {latest['date']}", file=sys.stderr)
        sys.exit(1)
    overall = next((ax for ax in latest["axes"] if ax["axis_name"] == "overall_risk"), None)
    if overall is None:
        print("FAIL: overall_risk not in latest axes", file=sys.stderr)
        sys.exit(1)
    if overall["label"] != "CRITICAL":
        print(f"FAIL: expected CRITICAL, got {overall['label']}", file=sys.stderr)
        sys.exit(1)
    if not overall["escalated"]:
        print("FAIL: expected escalated=True", file=sys.stderr)
        sys.exit(1)

    # ---- Test 2: 404 for unknown server ----
    resp2 = client.get("/api/scoring/timeline/history/no-such-server", params={"days": 7})
    if resp2.status_code != 404:
        print(f"FAIL: expected 404, got {resp2.status_code}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 3: timeline for server with one score ----
    resp3 = client.get("/api/scoring/timeline/history/srv2", params={"days": 7})
    if resp3.status_code != 200:
        print(f"FAIL: expected 200 for srv2, got {resp3.status_code}", file=sys.stderr)
        sys.exit(1)
    if len(resp3.json()["snapshots"]) != 1:
        print(f"FAIL: expected 1 snapshot for srv2, got {len(resp3.json()['snapshots'])}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 4: distribution across all servers ----
    resp4 = client.get("/api/scoring/timeline/distribution", params={"days": 7})
    if resp4.status_code != 200:
        print(f"FAIL: distribution 200, got {resp4.status_code}: {resp4.text}", file=sys.stderr)
        sys.exit(1)
    dist_data = resp4.json()
    if not dist_data.get("axes"):
        print("FAIL: distribution has no axes", file=sys.stderr)
        sys.exit(1)
    overall_ax = next((ax for ax in dist_data["axes"] if ax["axis_name"] == "overall_risk"), None)
    if overall_ax is None:
        print("FAIL: overall_risk not in distribution", file=sys.stderr)
        sys.exit(1)
    if overall_ax["total_servers"] != 2:
        print(f"FAIL: expected 2 servers in overall_risk dist, got {overall_ax['total_servers']}", file=sys.stderr)
        sys.exit(1)
    labels = {b["label"] for b in overall_ax["buckets"]}
    if "CRITICAL" not in labels or "LOW" not in labels:
        print(f"FAIL: expected CRITICAL+LOW buckets, got {labels}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 5: distribution filtered to single axis ----
    resp5 = client.get("/api/scoring/timeline/distribution", params={"days": 7, "axis": "overall_risk"})
    if resp5.status_code != 200:
        print(f"FAIL: filtered distribution 200, got {resp5.status_code}: {resp5.text}", file=sys.stderr)
        sys.exit(1)
    filtered = resp5.json()
    if len(filtered["axes"]) != 1:
        print(f"FAIL: expected 1 axis, got {len(filtered['axes'])}", file=sys.stderr)
        sys.exit(1)
    if filtered["axes"][0]["axis_name"] != "overall_risk":
        print(f"FAIL: wrong axis {filtered['axes'][0]['axis_name']}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 6: invalid axis ----
    resp6 = client.get("/api/scoring/timeline/distribution", params={"days": 7, "axis": "not_a_real_axis"})
    if resp6.status_code != 400:
        print(f"FAIL: expected 400 for invalid axis, got {resp6.status_code}", file=sys.stderr)
        sys.exit(1)

    # ---- Test 7: days validation ----
    resp7 = client.get("/api/scoring/timeline/history/srv1", params={"days": 0})
    if resp7.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {resp7.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
