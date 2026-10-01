# deps: fastapi, pydantic, sqlalchemy
"""Server Scoring History Service.

Returns historical axis score data for MCP servers: per-day snapshots,
latest summary, and trend indicators.

Public endpoint (auth=public).  Reads from mcp_llm_axis_scores and
mcp_server_registry via app.db.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_scoring_history"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class AxisScoreEntry(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    escalated_to: Optional[str]
    model_version: str
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DaySnapshot(BaseModel):
    date: str
    axes: list[AxisScoreEntry]


class ServerScoringHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    days: int
    series: list[DaySnapshot]


class AxisSummaryEntry(BaseModel):
    axis_name: str
    latest_label: Optional[str]
    latest_p_top: Optional[float]
    score_count: int
    first_scored_at: Optional[datetime]
    latest_scored_at: Optional[datetime]


class ServerScoringSummaryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    summary: list[AxisSummaryEntry]


class TrendPoint(BaseModel):
    axis_name: str
    date: str
    label: str
    p_top: Optional[float]


class ServerScoringTrendResponse(BaseModel):
    server_id: str
    name: Optional[str]
    trend: list[TrendPoint]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label_to_tier(label: Optional[str]) -> str:
    """Normalize axis score label to a risk tier string."""
    if label is None:
        return "UNKNOWN"
    l = label.upper()
    if l in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED"):
        return l
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/server-scoring-history/{server_id}",
    response_model=ServerScoringHistoryResponse,
    summary="Get per-day scoring history for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_scoring_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ServerScoringHistoryResponse:
    """
    Returns per-day axis score snapshots for the given server over the past *days*.
    """
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    # Group by calendar date
    by_date: dict[str, list[McpLlmAxisScore]] = {}
    for row in rows:
        key = row.scored_at.strftime("%Y-%m-%d")
        by_date.setdefault(key, []).append(row)

    series = [
        DaySnapshot(
            date=date,
            axes=[
                AxisScoreEntry(
                    axis_name=r.axis_name,
                    label=r.label,
                    label_index=r.label_index,
                    p_top=r.p_top,
                    p_critical=r.p_critical,
                    p_danger=r.p_danger,
                    escalated=r.escalated,
                    escalated_to=r.escalated_to,
                    model_version=r.model_version,
                    scored_at=r.scored_at,
                )
                for r in sorted(axis_rows, key=lambda x: x.axis_name)
            ],
        )
        for date, axis_rows in sorted(by_date.items(), reverse=True)
    ]

    return ServerScoringHistoryResponse(
        server_id=server_id,
        name=server.name,
        days=days,
        series=series,
    )


@router.get(
    "/server-scoring-history/{server_id}/summary",
    response_model=ServerScoringSummaryResponse,
    summary="Get score summary for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_scoring_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerScoringSummaryResponse:
    """
    Returns the latest axis score summary per axis for a server.
    """
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    # Subquery: latest scored_at per axis
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
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
        )
        .all()
    )

    # Fallback counts for axes with no summary row match
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

    covered = {r.axis_name for r in summary_rows}
    summary_map: dict[str, AxisSummaryEntry] = {}
    for r in summary_rows:
        summary_map[r.axis_name] = AxisSummaryEntry(
            axis_name=r.axis_name,
            latest_label=r.latest_label,
            latest_p_top=r.latest_p_top,
            score_count=r.score_count,
            first_scored_at=r.first_scored_at,
            latest_scored_at=r.latest_scored_at,
        )
    for r in all_counts:
        if r.axis_name not in covered:
            summary_map[r.axis_name] = AxisSummaryEntry(
                axis_name=r.axis_name,
                latest_label=None,
                latest_p_top=None,
                score_count=r.score_count,
                first_scored_at=r.first_scored_at,
                latest_scored_at=r.latest_scored_at,
            )

    return ServerScoringSummaryResponse(
        server_id=server_id,
        name=server.name,
        summary=list(summary_map.values()),
    )


@router.get(
    "/server-scoring-history/{server_id}/trend",
    response_model=ServerScoringTrendResponse,
    summary="Get score trend over time for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_scoring_trend(
    server_id: str,
    axis_name: str = Query(
        default="overall_risk",
        description="Axis to get trend for",
    ),
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ServerScoringTrendResponse:
    """
    Returns the per-day score trend for a specific axis on a server.
    """
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
            label=_label_to_tier(s.label),
            p_top=s.p_top,
        )
        for s in scores
    ]

    return ServerScoringTrendResponse(
        server_id=server_id,
        name=server.name,
        trend=trend,
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

    from app.models import Base
    from app.db import get_session as _orig_get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[_orig_get_session] = _override

    now = datetime.now(timezone.utc)
    d1 = now - timedelta(days=1)
    d2 = now - timedelta(days=2)

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="srv1", name="Test Server", risk_tier="HIGH",
            verdict="clean", confidence=0.9,
        ))
        sess.add(McpServerRegistry(
            server_id="srv2", name="Other Server", risk_tier="LOW",
            verdict="clean", confidence=0.8,
        ))
        # SQLite + BigInteger autoincrement workaround: assign IDs explicitly
        sess.add(McpLlmAxisScore(
            id=1,
            server_id="srv1", axis_name="overall_risk", label="HIGH",
            p_top=0.75, p_critical=0.1, p_danger=0.15,
            model_version="v1-old", adapter_sha256="a1", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            id=2,
            server_id="srv1", axis_name="auth_strength", label="MEDIUM",
            p_top=0.45, p_critical=0.2, p_danger=0.35,
            model_version="v1-old", adapter_sha256="a1", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            id=3,
            server_id="srv1", axis_name="overall_risk", label="CRITICAL",
            p_top=0.88, p_critical=0.05, p_danger=0.07,
            model_version="v1", adapter_sha256="a2", decision_rule_version="r1",
            scored_at=d1, escalated=True,
        ))
        sess.add(McpLlmAxisScore(
            id=4,
            server_id="srv1", axis_name="auth_strength", label="LOW",
            p_top=0.30, p_critical=0.3, p_danger=0.4,
            model_version="v1", adapter_sha256="a2", decision_rule_version="r1",
            scored_at=d1, escalated=False,
        ))
        sess.commit()

    client = TestClient(test_app)

    # Test 1: happy path — scoring history for srv1
    resp = client.get("/api/server-scoring-history/srv1", params={"days": 7})
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv1":
        print(f"FAIL: wrong server_id {data['server_id']}", file=sys.stderr)
        sys.exit(1)
    if len(data["series"]) != 2:
        print(f"FAIL: expected 2 days, got {len(data['series'])}", file=sys.stderr)
        sys.exit(1)
    # Latest day should be first (descending)
    latest = data["series"][0]
    if latest["date"] != d1.strftime("%Y-%m-%d"):
        print(f"FAIL: expected latest date {d1.date()}, got {latest['date']}", file=sys.stderr)
        sys.exit(1)
    axis_names = {ax["axis_name"] for ax in latest["axes"]}
    if "overall_risk" not in axis_names:
        print(f"FAIL: overall_risk not in axes", file=sys.stderr)
        sys.exit(1)

    # Test 2: 404 for unknown server
    resp2 = client.get("/api/server-scoring-history/no-such-server", params={"days": 7})
    if resp2.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp2.status_code}", file=sys.stderr)
        sys.exit(1)

    # Test 3: empty series for server with no scores
    resp3 = client.get("/api/server-scoring-history/srv2", params={"days": 7})
    if resp3.status_code != 200:
        print(f"FAIL: expected 200 for srv2 (no scores), got {resp3.status_code}", file=sys.stderr)
        sys.exit(1)
    if len(resp3.json()["series"]) != 0:
        print(f"FAIL: expected 0 series for srv2", file=sys.stderr)
        sys.exit(1)

    # Test 4: days param validation — out of range
    resp4 = client.get("/api/server-scoring-history/srv1", params={"days": 0})
    if resp4.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {resp4.status_code}", file=sys.stderr)
        sys.exit(1)

    # Test 5: score summary
    resp5 = client.get("/api/server-scoring-history/srv1/summary")
    if resp5.status_code != 200:
        print(f"FAIL: summary endpoint returned {resp5.status_code}: {resp5.text}", file=sys.stderr)
        sys.exit(1)
    summ = resp5.json()
    if len(summ["summary"]) != 2:
        print(f"FAIL: expected 2 axes in summary, got {len(summ['summary'])}", file=sys.stderr)
        sys.exit(1)

    # Test 6: score trend
    resp6 = client.get(
        "/api/server-scoring-history/srv1/trend",
        params={"axis_name": "overall_risk", "days": 7},
    )
    if resp6.status_code != 200:
        print(f"FAIL: trend endpoint returned {resp6.status_code}: {resp6.text}", file=sys.stderr)
        sys.exit(1)
    trend = resp6.json()
    if len(trend["trend"]) != 2:
        print(f"FAIL: expected 2 trend points, got {len(trend['trend'])}", file=sys.stderr)
        sys.exit(1)
    if trend["trend"][0]["label"] != "HIGH":
        print(f"FAIL: expected first label HIGH, got {trend['trend'][0]['label']}", file=sys.stderr)
        sys.exit(1)
    if trend["trend"][1]["label"] != "CRITICAL":
        print(f"FAIL: expected second label CRITICAL, got {trend['trend'][1]['label']}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
