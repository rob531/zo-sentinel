# deps: fastapi, pydantic, sqlalchemy
"""Risk History Service.

Returns historical risk-tier and axis-score data for MCP servers:
  - per-server transition history (PerspectiveEvent + axis scores)
  - aggregate transitions summary across all servers
  - risk tier distribution snapshots over time

Public endpoint (auth=public per the directive).
Data: app Postgres via SQLAlchemy Session (from app.db import get_session).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import asc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["risk_history"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class AxisSnapshotEntry(BaseModel):
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]

    model_config = ConfigDict(from_attributes=True)


class RiskHistoryEntry(BaseModel):
    """One point-in-time record: tier verdict + all axis scores at scored_at."""
    event_id: int
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    scored_at: datetime
    axes: list[AxisSnapshotEntry]


class ServerRiskHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    entries: list[RiskHistoryEntry]


class TransitionBucket(BaseModel):
    from_tier: str
    to_tier: str
    count: int


class RiskHistorySummaryResponse(BaseModel):
    total_servers: int
    servers_with_transitions: int
    total_transitions: int
    top_transitions: list[TransitionBucket]
    lookback_days: int


class TierDistributionPoint(BaseModel):
    date: str
    CRITICAL: int = 0
    HIGH: int = 0
    MEDIUM: int = 0
    LOW: int = 0
    MINIMAL: int = 0
    UNKNOWN: int = 0


class RiskHistoryTrendResponse(BaseModel):
    server_id: str
    name: Optional[str]
    distribution_over_time: list[TierDistributionPoint]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tier_from_label(label: Optional[str]) -> str:
    """Normalize axis score label to tier string."""
    if label is None:
        return "UNKNOWN"
    l = label.upper()
    if l in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED"):
        return l
    return "UNKNOWN"


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    """Return the most recent model_version for this server, or None."""
    row = db.query(func.max(McpLlmAxisScore.model_version)).filter(
        McpLlmAxisScore.server_id == server_id
    ).scalar()
    return row  # type: ignore[return-value]


def _scores_at_or_before(
    db: Session, server_id: str, model_version: str, scored_at: datetime
) -> list[AxisSnapshotEntry]:
    """Return axis scores closest to scored_at for a given model_version."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
            McpLlmAxisScore.scored_at <= scored_at,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: set[str] = set()
    entries: list[AxisSnapshotEntry] = []
    for r in rows:
        if r.axis_name not in seen:
            seen.add(r.axis_name)
            entries.append(AxisSnapshotEntry(
                axis_name=r.axis_name,
                label=r.label,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                escalated=r.escalated,
            ))
    return entries


def _build_entries(
    db: Session, server_id: str, events: list[PerspectiveEvent]
) -> list[RiskHistoryEntry]:
    """For each perspective event, pick the nearest axis-score snapshot."""
    model_version = _latest_model_version(db, server_id)
    if not model_version:
        return [
            RiskHistoryEntry(
                event_id=e.id,
                change_type=e.change_type,
                old_tier=e.old_tier,
                new_tier=e.new_tier,
                scored_at=e.created_at,
                axes=[],
            )
            for e in events
        ]

    entries: list[RiskHistoryEntry] = []
    for ev in events:
        axes = _scores_at_or_before(db, server_id, model_version, ev.created_at)
        entries.append(RiskHistoryEntry(
            event_id=ev.id,
            change_type=ev.change_type,
            old_tier=ev.old_tier,
            new_tier=ev.new_tier,
            scored_at=ev.created_at,
            axes=axes,
        ))
    return entries


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/risk-history/{server_id}",
    response_model=ServerRiskHistoryResponse,
    summary="Get risk history for a server",
    responses={404: {"description": "Server not found"}},
)
def get_risk_history(
    server_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> ServerRiskHistoryResponse:
    """
    Returns the chronological risk-tier transition history for a server.
    Each entry pairs a PerspectiveEvent (tier change) with the axis-score
    snapshot nearest in time.
    """
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    events = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(asc(PerspectiveEvent.created_at))
        .limit(limit)
        .all()
    )

    entries = _build_entries(db, server_id, events)

    return ServerRiskHistoryResponse(
        server_id=server_id,
        name=srv.name,
        risk_tier=srv.risk_tier,
        entries=entries,
    )


@router.get(
    "/risk-history/{server_id}/latest",
    response_model=RiskHistoryEntry,
    summary="Get the most recent risk history entry for a server",
    responses={404: {"description": "Server not found"}},
)
def get_latest_risk_entry(
    server_id: str,
    db: Session = Depends(get_session),
) -> RiskHistoryEntry:
    """
    Returns the latest PerspectiveEvent for a server plus its nearest
    axis-score snapshot.
    """
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    ev = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(asc(PerspectiveEvent.created_at))
        .first()
    )

    if not ev:
        raise HTTPException(
            status_code=404,
            detail=f"No risk history found for server {server_id}",
        )

    entries = _build_entries(db, server_id, [ev])
    return entries[0]


@router.get(
    "/risk-history/summary",
    response_model=RiskHistorySummaryResponse,
    summary="Get aggregate risk transition summary",
)
def get_risk_history_summary(
    period_days: int = Query(default=30, ge=1, le=365),
    top_n: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_session),
) -> RiskHistorySummaryResponse:
    """
    Returns an aggregate summary of risk tier transitions across all servers
    for the specified period.
    """
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    recent_events = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.created_at >= cutoff)
        .all()
    )

    servers_with_transitions = {e.server_id for e in recent_events if e.old_tier != e.new_tier}

    # Count transition pairs
    transition_counts: dict[tuple[str, str], int] = {}
    for ev in recent_events:
        if ev.old_tier and ev.new_tier and ev.old_tier != ev.new_tier:
            key = (_tier_from_label(ev.old_tier), _tier_from_label(ev.new_tier))
            transition_counts[key] = transition_counts.get(key, 0) + 1

    total_transitions = sum(transition_counts.values())

    top_transitions = [
        TransitionBucket(from_tier=k[0], to_tier=k[1], count=v)
        for k, v in sorted(transition_counts.items(), key=lambda x: -x[1])[:top_n]
    ]

    return RiskHistorySummaryResponse(
        total_servers=total_servers,
        servers_with_transitions=len(servers_with_transitions),
        total_transitions=total_transitions,
        top_transitions=top_transitions,
        lookback_days=period_days,
    )


@router.get(
    "/risk-history/{server_id}/trend",
    response_model=RiskHistoryTrendResponse,
    summary="Get risk tier trend over time for a server",
    responses={404: {"description": "Server not found"}},
)
def get_risk_trend(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> RiskHistoryTrendResponse:
    """
    Returns the risk tier distribution across axes over time for a server.
    """
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=period_days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    # Build daily distribution snapshots
    daily_axes: dict[str, dict[str, int]] = {}
    for s in scores:
        day = s.scored_at.date().isoformat() if s.scored_at else ""
        if day not in daily_axes:
            daily_axes[day] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "MINIMAL": 0, "UNKNOWN": 0}
        tier = _tier_from_label(s.label)
        if tier in daily_axes[day]:
            daily_axes[day][tier] += 1

    distribution_over_time = [
        TierDistributionPoint(date=day, **counts)
        for day, counts in sorted(daily_axes.items())
    ]

    return RiskHistoryTrendResponse(
        server_id=server_id,
        name=srv.name,
        distribution_over_time=distribution_over_time,
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

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

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
    t1 = now - timedelta(days=2)
    t2 = now - timedelta(days=1)
    t3 = now

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="srv-history-1", name="Risk Test Server", risk_tier="HIGH",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-history-2", name="No Events Server", risk_tier="LOW",
        ))
        # 3 events for srv-history-1
        sess.add(PerspectiveEvent(
            id=20, server_id="srv-history-1", change_type="initial",
            old_tier=None, new_tier="LOW", created_at=t1,
        ))
        sess.add(PerspectiveEvent(
            id=21, server_id="srv-history-1", change_type="tier_upgrade",
            old_tier="LOW", new_tier="MEDIUM", created_at=t2,
        ))
        sess.add(PerspectiveEvent(
            id=22, server_id="srv-history-1", change_type="tier_upgrade",
            old_tier="MEDIUM", new_tier="HIGH", created_at=t3,
        ))
        # axis scores
        sess.add(McpLlmAxisScore(
            server_id="srv-history-1", axis_name="overall_risk",
            label="LOW", p_top=0.15, model_version="v1", scored_at=t1,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-history-1", axis_name="overall_risk",
            label="MEDIUM", p_top=0.45, model_version="v1", scored_at=t2,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-history-1", axis_name="overall_risk",
            label="HIGH", p_top=0.65, model_version="v1", scored_at=t3,
        ))
        sess.commit()

    client = TestClient(test_app)

    # Test 1: full history
    resp = client.get("/api/risk-history/srv-history-1")
    if resp.status_code != 200:
        print(f"FAIL: history endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv-history-1":
        print(f"FAIL: wrong server_id: {data['server_id']}")
        sys.exit(1)
    if len(data["entries"]) != 3:
        print(f"FAIL: expected 3 entries, got {len(data['entries'])}")
        sys.exit(1)
    if data["entries"][0]["new_tier"] != "LOW":
        print(f"FAIL: first entry wrong new_tier: {data['entries'][0]['new_tier']}")
        sys.exit(1)
    if data["entries"][1]["old_tier"] != "LOW":
        print(f"FAIL: second entry wrong old_tier: {data['entries'][1]['old_tier']}")
        sys.exit(1)

    # Test 2: latest entry
    resp2 = client.get("/api/risk-history/srv-history-1/latest")
    if resp2.status_code != 200:
        print(f"FAIL: latest endpoint returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    latest = resp2.json()
    if latest["change_type"] != "tier_upgrade":
        print(f"FAIL: latest change_type wrong: {latest['change_type']}")
        sys.exit(1)
    if latest["new_tier"] != "HIGH":
        print(f"FAIL: latest new_tier wrong: {latest['new_tier']}")
        sys.exit(1)

    # Test 3: 404 unknown server
    resp3 = client.get("/api/risk-history/nonexistent-server")
    if resp3.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp3.status_code}")
        sys.exit(1)

    # Test 4: empty history (server with no events)
    resp4 = client.get("/api/risk-history/srv-history-2")
    if resp4.status_code != 200:
        print(f"FAIL: empty history returned {resp4.status_code}")
        sys.exit(1)
    if len(resp4.json()["entries"]) != 0:
        print(f"FAIL: expected 0 entries for srv-history-2")
        sys.exit(1)

    # Test 5: latest 404 for server with no events
    resp5 = client.get("/api/risk-history/srv-history-2/latest")
    if resp5.status_code != 404:
        print(f"FAIL: expected 404 for latest on empty server, got {resp5.status_code}")
        sys.exit(1)

    # Test 6: summary
    resp6 = client.get("/api/risk-history/summary?period_days=7")
    if resp6.status_code != 200:
        print(f"FAIL: summary endpoint returned {resp6.status_code}: {resp6.text}")
        sys.exit(1)
    summary = resp6.json()
    if summary["total_transitions"] != 2:
        print(f"FAIL: expected 2 total transitions, got {summary['total_transitions']}")
        sys.exit(1)

    # Test 7: trend
    resp7 = client.get("/api/risk-history/srv-history-1/trend?period_days=7")
    if resp7.status_code != 200:
        print(f"FAIL: trend endpoint returned {resp7.status_code}: {resp7.text}")
        sys.exit(1)
    trend = resp7.json()
    if len(trend["distribution_over_time"]) != 3:
        print(f"FAIL: expected 3 trend points, got {len(trend['distribution_over_time'])}")
        sys.exit(1)

    print("PASS")
