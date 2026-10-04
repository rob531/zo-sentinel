# deps: fastapi, sqlalchemy
"""Server Score Timeline API.

Returns the historical axis score timeline for a given server.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_score_timeline_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AxisScoreEntry(BaseModel):
    """A single axis score at a point in time."""
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


class ScoreTimelineResponse(BaseModel):
    """Score timeline for a server over the requested period."""
    server_id: str
    name: Optional[str]
    days: int
    total_scores: int
    timeline: list[AxisScoreEntry]


class ScoreSummaryByAxis(BaseModel):
    """Aggregated stats per axis over the timeline window."""
    axis_name: str
    score_count: int
    latest_label: Optional[str]
    latest_p_top: Optional[float]
    latest_scored_at: Optional[datetime]


class ScoreSummaryResponse(BaseModel):
    """Summary of score history for a server."""
    server_id: str
    name: Optional[str]
    days: int
    total_events: int
    axes: list[ScoreSummaryByAxis]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/server_score_timeline/{server_id}",
    response_model=ScoreTimelineResponse,
    summary="Get score timeline for a server",
    responses={404: {"description": "Server not found"}},
)
def get_score_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ScoreTimelineResponse:
    """Return all axis score events for a server within the period."""
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    timeline = [
        AxisScoreEntry(
            axis_name=s.axis_name,
            label=s.label,
            label_index=s.label_index,
            p_top=s.p_top,
            p_critical=s.p_critical,
            p_danger=s.p_danger,
            escalated=bool(s.escalated),
            model_version=s.model_version or "",
            scored_at=s.scored_at,
        )
        for s in scores
    ]

    return ScoreTimelineResponse(
        server_id=server_id,
        name=srv.name,
        days=days,
        total_scores=len(timeline),
        timeline=timeline,
    )


@router.get(
    "/server_score_timeline/{server_id}/summary",
    response_model=ScoreSummaryResponse,
    summary="Get score summary by axis for a server",
    responses={404: {"description": "Server not found"}},
)
def get_score_summary(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ScoreSummaryResponse:
    """Return aggregated score stats per axis for the period."""
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get all scores in window, grouped by axis
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc())
        .all()
    )

    # Group by axis and pick latest
    axis_map: dict[str, McpLlmAxisScore] = {}
    axis_counts: dict[str, int] = {}
    for s in scores:
        axis_map.setdefault(s.axis_name, s)
        axis_counts[s.axis_name] = axis_counts.get(s.axis_name, 0) + 1

    axes = [
        ScoreSummaryByAxis(
            axis_name=an,
            score_count=axis_counts.get(an, 0),
            latest_label=axis_map[an].label,
            latest_p_top=axis_map[an].p_top,
            latest_scored_at=axis_map[an].scored_at,
        )
        for an in sorted(axis_map.keys())
    ]

    return ScoreSummaryResponse(
        server_id=server_id,
        name=srv.name,
        days=days,
        total_events=len(scores),
        axes=axes,
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
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)

    # Seed test data
    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="tl-srv-1",
            name="Timeline Server",
            risk_tier="HIGH",
            verdict="clean",
            confidence=0.9,
        ))
        sess.add(McpServerRegistry(
            server_id="tl-srv-2",
            name="Stable Server",
            risk_tier="LOW",
            verdict="clean",
            confidence=0.8,
        ))
        # Day 3
        sess.add(McpLlmAxisScore(
            server_id="tl-srv-1",
            axis_name="overall_risk",
            label="MEDIUM",
            p_top=0.45,
            p_critical=0.1,
            p_danger=0.3,
            escalated=False,
            model_version="v1",
            adapter_sha256="sha1",
            decision_rule_version="r1",
            scored_at=now - timedelta(days=3),
        ))
        sess.add(McpLlmAxisScore(
            server_id="tl-srv-1",
            axis_name="auth_strength",
            label="LOW",
            p_top=0.25,
            p_critical=0.2,
            p_danger=0.5,
            escalated=False,
            model_version="v1",
            adapter_sha256="sha1",
            decision_rule_version="r1",
            scored_at=now - timedelta(days=3),
        ))
        # Day 2
        sess.add(McpLlmAxisScore(
            server_id="tl-srv-1",
            axis_name="overall_risk",
            label="HIGH",
            p_top=0.70,
            p_critical=0.1,
            p_danger=0.2,
            escalated=True,
            model_version="v1",
            adapter_sha256="sha2",
            decision_rule_version="r1",
            scored_at=now - timedelta(days=2),
        ))
        sess.add(McpLlmAxisScore(
            server_id="tl-srv-1",
            axis_name="auth_strength",
            label="MEDIUM",
            p_top=0.50,
            p_critical=0.2,
            p_danger=0.3,
            escalated=False,
            model_version="v1",
            adapter_sha256="sha2",
            decision_rule_version="r1",
            scored_at=now - timedelta(days=2),
        ))
        # Day 1
        sess.add(McpLlmAxisScore(
            server_id="tl-srv-1",
            axis_name="overall_risk",
            label="CRITICAL",
            p_top=0.88,
            p_critical=0.05,
            p_danger=0.07,
            escalated=True,
            model_version="v1",
            adapter_sha256="sha3",
            decision_rule_version="r1",
            scored_at=now - timedelta(days=1),
        ))
        # tl-srv-2 — single score
        sess.add(McpLlmAxisScore(
            server_id="tl-srv-2",
            axis_name="overall_risk",
            label="LOW",
            p_top=0.20,
            p_critical=0.05,
            p_danger=0.10,
            escalated=False,
            model_version="v1",
            adapter_sha256="sha4",
            decision_rule_version="r1",
            scored_at=now - timedelta(days=1),
        ))
        sess.commit()

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    # Test 1: timeline endpoint — 200 with data
    r = client.get("/api/server_score_timeline/tl-srv-1?days=7")
    if r.status_code != 200:
        print(f"FAIL: timeline 200, got {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    d = r.json()
    if d["server_id"] != "tl-srv-1":
        print(f"FAIL: wrong server_id {d['server_id']}", file=sys.stderr)
        sys.exit(1)
    if d["name"] != "Timeline Server":
        print(f"FAIL: wrong name {d['name']}", file=sys.stderr)
        sys.exit(1)
    if d["total_scores"] != 5:
        print(f"FAIL: expected 5 scores, got {d['total_scores']}", file=sys.stderr)
        sys.exit(1)
    # Most recent first
    if d["timeline"][0]["label"] != "CRITICAL":
        print(f"FAIL: expected first label CRITICAL, got {d['timeline'][0]['label']}", file=sys.stderr)
        sys.exit(1)
    if d["timeline"][0]["escalated"] is not True:
        print("FAIL: first entry should be escalated", file=sys.stderr)
        sys.exit(1)

    # Test 2: timeline 404
    r2 = client.get("/api/server_score_timeline/no-such-srv?days=7")
    if r2.status_code != 404:
        print(f"FAIL: expected 404, got {r2.status_code}", file=sys.stderr)
        sys.exit(1)

    # Test 3: summary endpoint — 200
    r3 = client.get("/api/server_score_timeline/tl-srv-1/summary?days=7")
    if r3.status_code != 200:
        print(f"FAIL: summary 200, got {r3.status_code}: {r3.text}", file=sys.stderr)
        sys.exit(1)
    s = r3.json()
    if s["server_id"] != "tl-srv-1":
        print(f"FAIL: summary wrong server_id {s['server_id']}", file=sys.stderr)
        sys.exit(1)
    if s["total_events"] != 5:
        print(f"FAIL: summary expected 5 events, got {s['total_events']}", file=sys.stderr)
        sys.exit(1)
    if len(s["axes"]) != 2:
        print(f"FAIL: expected 2 axes, got {len(s['axes'])}", file=sys.stderr)
        sys.exit(1)
    # Latest labels
    overall = next((a for a in s["axes"] if a["axis_name"] == "overall_risk"), None)
    if overall is None:
        print("FAIL: no overall_risk in summary", file=sys.stderr)
        sys.exit(1)
    if overall["latest_label"] != "CRITICAL":
        print(f"FAIL: latest_label expected CRITICAL, got {overall['latest_label']}", file=sys.stderr)
        sys.exit(1)

    # Test 4: days validation — 422
    r4 = client.get("/api/server_score_timeline/tl-srv-1?days=0")
    if r4.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {r4.status_code}", file=sys.stderr)
        sys.exit(1)

    # Test 5: stable server (no evolution data in window)
    r5 = client.get("/api/server_score_timeline/tl-srv-2/summary?days=7")
    if r5.status_code != 200:
        print(f"FAIL: tl-srv-2 summary 200, got {r5.status_code}", file=sys.stderr)
        sys.exit(1)
    if r5.json()["total_events"] != 1:
        print(f"FAIL: tl-srv-2 should have 1 event, got {r5.json()['total_events']}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
