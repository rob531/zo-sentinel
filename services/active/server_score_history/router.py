# deps: fastapi, pydantic, sqlalchemy
"""Server Score History Service.

Returns historical axis score data for MCP servers: per-axis scores over time,
latest summary, and trend indicators.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_score_history"])


# --- Pydantic response models ---

class AxisScoreEntry(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    model_version: str
    scored_at: datetime


class ServerScoreHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    scores: list[AxisScoreEntry]


class AxisSummaryEntry(BaseModel):
    axis_name: str
    latest_label: Optional[str]
    latest_p_top: Optional[float]
    score_count: int
    first_scored_at: Optional[datetime]
    latest_scored_at: Optional[datetime]


class ServerScoreSummaryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    summary: list[AxisSummaryEntry]


class TrendPoint(BaseModel):
    axis_name: str
    date: str
    label: str
    p_top: Optional[float]


class ServerScoreTrendResponse(BaseModel):
    server_id: str
    name: Optional[str]
    trend: list[TrendPoint]


# --- Helper functions ---

def _tier_from_p_top(p_top: Optional[float]) -> str:
    """Map p_top probability to a risk tier label."""
    if p_top is None:
        return "UNKNOWN"
    if p_top >= 0.8:
        return "CRITICAL"
    if p_top >= 0.6:
        return "HIGH"
    if p_top >= 0.4:
        return "MEDIUM"
    if p_top >= 0.2:
        return "LOW"
    return "MINIMAL"


def _tier_from_label(label: Optional[str]) -> str:
    """Normalize axis score label to tier string."""
    if label is None:
        return "UNKNOWN"
    l = label.upper()
    if l in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED"):
        return l
    return "UNKNOWN"


# --- Endpoints ---

@router.get(
    "/server-score-history/{server_id}",
    response_model=ServerScoreHistoryResponse,
    summary="Get full score history for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_score_history(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ServerScoreHistoryResponse:
    """
    Returns the complete axis score history for a server over the specified period.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=period_days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    entries = [
        AxisScoreEntry(
            axis_name=s.axis_name,
            label=s.label,
            label_index=s.label_index,
            p_top=s.p_top,
            p_critical=s.p_critical,
            p_danger=s.p_danger,
            escalated=s.escalated,
            model_version=s.model_version,
            scored_at=s.scored_at,
        )
        for s in scores
    ]

    return ServerScoreHistoryResponse(
        server_id=server_id,
        name=server.name,
        scores=entries,
    )


@router.get(
    "/server-score-history/{server_id}/summary",
    response_model=ServerScoreSummaryResponse,
    summary="Get score summary for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_score_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerScoreSummaryResponse:
    """
    Returns the latest axis score summary per axis for a server.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    # Subquery: latest score per axis
    latest_subq = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    summary_rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label.label("latest_label"),
            McpLlmAxisScore.p_top.label("latest_p_top"),
            func.count(McpLlmAxisScore.id).label("score_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .join(latest_subq, McpLlmAxisScore.axis_name == latest_subq.c.axis_name)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at == latest_subq.c.latest_at)
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label, McpLlmAxisScore.p_top)
        .all()
    )

    # Fallback: count-only for axes with no latest match
    all_counts = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("score_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .all()
    )

    covered_axes = {r.axis_name for r in summary_rows}
    summary_map = {
        r.axis_name: AxisSummaryEntry(
            axis_name=r.axis_name,
            latest_label=r.latest_label,
            latest_p_top=r.latest_p_top,
            score_count=r.score_count,
            first_scored_at=r.first_scored_at,
            latest_scored_at=r.latest_scored_at,
        )
        for r in summary_rows
    }
    for r in all_counts:
        if r.axis_name not in covered_axes:
            summary_map[r.axis_name] = AxisSummaryEntry(
                axis_name=r.axis_name,
                latest_label=None,
                latest_p_top=None,
                score_count=r.score_count,
                first_scored_at=r.first_scored_at,
                latest_scored_at=r.latest_scored_at,
            )

    return ServerScoreSummaryResponse(
        server_id=server_id,
        name=server.name,
        summary=list(summary_map.values()),
    )


@router.get(
    "/server-score-history/{server_id}/trend",
    response_model=ServerScoreTrendResponse,
    summary="Get score trend over time for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_score_trend(
    server_id: str,
    axis_name: str = Query(default="overall_risk", description="Axis to get trend for"),
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerScoreTrendResponse:
    """
    Returns the per-day score trend for a specific axis on a server.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=period_days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.asc())
        .all()
    )

    trend = [
        TrendPoint(
            axis_name=s.axis_name,
            date=s.scored_at.isoformat() if s.scored_at else "",
            label=_tier_from_label(s.label),
            p_top=s.p_top,
        )
        for s in scores
    ]

    return ServerScoreTrendResponse(
        server_id=server_id,
        name=server.name,
        trend=trend,
    )


# --- Self-test ---

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(test_engine)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()
    day1 = now - timedelta(days=2)
    day2 = now - timedelta(days=1)

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(server_id="srv-test-1", name="Test Server 1", risk_tier="HIGH"))
        sess.add(McpServerRegistry(server_id="srv-test-2", name="Test Server 2", risk_tier="MEDIUM"))
        sess.add(McpLlmAxisScore(
            server_id="srv-test-1", axis_name="overall_risk", label="MEDIUM",
            p_top=0.45, model_version="v1", scored_at=day1,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-test-1", axis_name="overall_risk", label="HIGH",
            p_top=0.65, model_version="v1", scored_at=day2,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-test-1", axis_name="auth_strength", label="WEAK",
            p_top=0.30, model_version="v1", scored_at=day2,
        ))
        sess.commit()

    client = TestClient(test_app)

    # Test 1: score history
    resp = client.get("/api/server-score-history/srv-test-1?period_days=7")
    if resp.status_code != 200:
        print(f"FAIL: history endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv-test-1":
        print(f"FAIL: wrong server_id: {data['server_id']}")
        sys.exit(1)
    if len(data["scores"]) != 3:
        print(f"FAIL: expected 3 scores, got {len(data['scores'])}")
        sys.exit(1)

    # Test 2: score summary
    resp2 = client.get("/api/server-score-history/srv-test-1/summary")
    if resp2.status_code != 200:
        print(f"FAIL: summary endpoint returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    summ = resp2.json()
    if len(summ["summary"]) != 2:
        print(f"FAIL: expected 2 axes in summary, got {len(summ['summary'])}")
        sys.exit(1)

    # Test 3: score trend
    resp3 = client.get("/api/server-score-history/srv-test-1/trend?axis_name=overall_risk&period_days=7")
    if resp3.status_code != 200:
        print(f"FAIL: trend endpoint returned {resp3.status_code}: {resp3.text}")
        sys.exit(1)
    trend = resp3.json()
    if len(trend["trend"]) != 2:
        print(f"FAIL: expected 2 trend points, got {len(trend['trend'])}")
        sys.exit(1)
    if trend["trend"][0]["label"] != "MEDIUM":
        print(f"FAIL: expected first label MEDIUM, got {trend['trend'][0]['label']}")
        sys.exit(1)
    if trend["trend"][1]["label"] != "HIGH":
        print(f"FAIL: expected second label HIGH, got {trend['trend'][1]['label']}")
        sys.exit(1)

    # Test 4: 404 for unknown server
    resp4 = client.get("/api/server-score-history/nonexistent-server?period_days=7")
    if resp4.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp4.status_code}")
        sys.exit(1)

    # Test 5: empty history for server with no scores
    resp5 = client.get("/api/server-score-history/srv-test-2?period_days=7")
    if resp5.status_code != 200:
        print(f"FAIL: empty history endpoint returned {resp5.status_code}")
        sys.exit(1)
    if len(resp5.json()["scores"]) != 0:
        print(f"FAIL: expected 0 scores for srv-test-2")
        sys.exit(1)

    print("PASS")
