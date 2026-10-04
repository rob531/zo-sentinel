# deps: fastapi, pydantic, sqlalchemy
"""
Risk Tier Transitions API.

Tracks how MCP servers move between risk tiers over time, returning
a transition matrix showing from_tier -> to_tier counts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, and_, cast, Date
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk_tier_transitions_api"])


# --- Pydantic request/response models ---

class TransitionEntry(BaseModel):
    from_tier: str
    to_tier: str
    count: int


class TransitionMatrixResponse(BaseModel):
    period_days: int
    total_transitions: int
    matrix: List[TransitionEntry]


class ServerTransitionHistory(BaseModel):
    server_id: str
    name: Optional[str]
    transitions: List[TransitionEntry]


class TierDistributionAtPoint(BaseModel):
    tier: str
    count: int
    pct: float


class TierDistributionResponse(BaseModel):
    date: str
    distribution: List[TierDistributionAtPoint]


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
    "/risk/tier-transitions/matrix",
    response_model=TransitionMatrixResponse,
    summary="Get risk tier transition matrix",
    responses={400: {"description": "Invalid period"}, 500: {"description": "Internal error"}},
)
def get_transition_matrix(
    period_days: int = Query(default=30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_session),
) -> TransitionMatrixResponse:
    """
    Returns a transition matrix showing how servers moved between risk tiers
    during the specified period.

    Each entry shows: from_tier -> to_tier with a count of such transitions.
    """
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    # Subquery: latest score per server per day
    daily_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            cast(McpLlmAxisScore.scored_at, Date).label("day"),
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            func.row_number()
            .over(
                partition_by=[McpLlmAxisScore.server_id, cast(McpLlmAxisScore.scored_at, Date)],
                order_by=McpLlmAxisScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )

    # Filter to latest row per server per day
    current_tiers = db.query(
        daily_latest.c.server_id,
        daily_latest.c.day,
        daily_latest.c.label,
        daily_latest.c.p_top,
    ).filter(daily_latest.c.rn == 1).subquery()

    # Lag to get previous day's tier
    with_prev = db.query(
        current_tiers.c.server_id,
        current_tiers.c.day,
        current_tiers.c.label.label("curr_label"),
        current_tiers.c.p_top.label("curr_p_top"),
        func.lag(current_tiers.c.label, 1)
        .over(partition_by=current_tiers.c.server_id, order_by=current_tiers.c.day)
        .label("prev_label"),
        func.lag(current_tiers.c.p_top, 1)
        .over(partition_by=current_tiers.c.server_id, order_by=current_tiers.c.day)
        .label("prev_p_top"),
    ).subquery()

    # Count transitions where tier changed
    transitions_q = (
        db.query(
            with_prev.c.prev_label,
            with_prev.c.curr_label,
            func.count().label("count"),
        )
        .filter(with_prev.c.prev_label.isnot(None))
        .filter(with_prev.c.prev_label != with_prev.c.curr_label)
        .group_by(with_prev.c.prev_label, with_prev.c.curr_label)
        .all()
    )

    matrix = [
        TransitionEntry(
            from_tier=_tier_from_label(row.prev_label),
            to_tier=_tier_from_label(row.curr_label),
            count=row.count,
        )
        for row in transitions_q
        if row.count > 0
    ]

    total = sum(e.count for e in matrix)

    return TransitionMatrixResponse(
        period_days=period_days,
        total_transitions=total,
        matrix=matrix,
    )


@router.get(
    "/risk/tier-transitions/servers/{server_id}",
    response_model=ServerTransitionHistory,
    summary="Get transition history for a specific server",
    responses={404: {"description": "Server not found"}},
)
def get_server_transition_history(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerTransitionHistory:
    """
    Returns the transition history for a specific server over the given period.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )

    cutoff = datetime.utcnow() - timedelta(days=period_days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    transitions: List[TransitionEntry] = []
    for i in range(1, len(scores)):
        prev_label = scores[i - 1].label
        curr_label = scores[i].label
        if prev_label != curr_label:
            transitions.append(
                TransitionEntry(
                    from_tier=_tier_from_label(prev_label),
                    to_tier=_tier_from_label(curr_label),
                    count=1,
                )
            )

    return ServerTransitionHistory(
        server_id=server_id,
        name=server.name,
        transitions=transitions,
    )


@router.get(
    "/risk/tier-transitions/tier-distribution",
    response_model=List[TierDistributionResponse],
    summary="Get risk tier distribution over time",
)
def get_tier_distribution(
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> List[TierDistributionResponse]:
    """
    Returns the distribution of servers across risk tiers for each day
    in the specified period.
    """
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    daily_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            cast(McpLlmAxisScore.scored_at, Date).label("day"),
            McpLlmAxisScore.label,
            func.row_number()
            .over(
                partition_by=[McpLlmAxisScore.server_id, cast(McpLlmAxisScore.scored_at, Date)],
                order_by=McpLlmAxisScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )

    current_tiers = db.query(
        daily_latest.c.day,
        daily_latest.c.label,
    ).filter(daily_latest.c.rn == 1).subquery()

    # Count per tier per day
    tier_counts = (
        db.query(
            current_tiers.c.day,
            current_tiers.c.label,
            func.count().label("count"),
        )
        .group_by(current_tiers.c.day, current_tiers.c.label)
        .order_by(current_tiers.c.day)
        .all()
    )

    # Organize by day
    by_day: dict = {}
    for row in tier_counts:
        day_str = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
        if day_str not in by_day:
            by_day[day_str] = []
        tier = _tier_from_label(row.label)
        by_day[day_str].append({"tier": tier, "count": row.count})

    # Calculate percentages and build response
    result: List[TierDistributionResponse] = []
    for day_str, tiers in sorted(by_day.items()):
        total = sum(t["count"] for t in tiers)
        dist = [
            TierDistributionAtPoint(
                tier=t["tier"],
                count=t["count"],
                pct=round(t["count"] / total * 100, 2) if total > 0 else 0.0,
            )
            for t in tiers
        ]
        result.append(TierDistributionResponse(date=day_str, distribution=dist))

    return result


# --- Self-test ---

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build an isolated test app with our router
    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite for self-test
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Create tables
    from app.models import Base

    Base.metadata.create_all(test_engine)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    # Seed test data
    with TestSessionLocal() as sess:
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        # Create test servers
        servers = [
            McpServerRegistry(server_id=f"srv-{i}", name=f"Server {i}", risk_tier="HIGH")
            for i in range(1, 6)
        ]
        sess.add_all(servers)
        sess.commit()

        # Server 1: CRITICAL -> HIGH (transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-1", axis_name="overall_risk", label="CRITICAL",
            p_top=0.85, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-1", axis_name="overall_risk", label="HIGH",
            p_top=0.65, model_version="v1", scored_at=yesterday,
        ))

        # Server 2: HIGH -> MEDIUM (transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-2", axis_name="overall_risk", label="HIGH",
            p_top=0.70, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-2", axis_name="overall_risk", label="MEDIUM",
            p_top=0.45, model_version="v1", scored_at=yesterday,
        ))

        # Server 3: MEDIUM -> MEDIUM (no transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-3", axis_name="overall_risk", label="MEDIUM",
            p_top=0.50, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-3", axis_name="overall_risk", label="MEDIUM",
            p_top=0.48, model_version="v1", scored_at=yesterday,
        ))

        # Server 4: LOW -> CRITICAL (transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-4", axis_name="overall_risk", label="LOW",
            p_top=0.15, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-4", axis_name="overall_risk", label="CRITICAL",
            p_top=0.88, model_version="v1", scored_at=yesterday,
        ))

        # Server 5: no scores (should be excluded)
        sess.commit()

    client = TestClient(test_app)

    # Test 1: transition matrix
    resp = client.get("/api/risk/tier-transitions/matrix?period_days=7")
    if resp.status_code != 200:
        print(f"FAIL: matrix endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["total_transitions"] != 3:
        print(f"FAIL: expected 3 total transitions, got {data['total_transitions']}")
        sys.exit(1)
    if len(data["matrix"]) != 3:
        print(f"FAIL: expected 3 matrix entries, got {len(data['matrix'])}")
        sys.exit(1)

    # Test 2: server transition history
    resp2 = client.get("/api/risk/tier-transitions/servers/srv-1?period_days=7")
    if resp2.status_code != 200:
        print(f"FAIL: server history endpoint returned {resp2.status_code}")
        sys.exit(1)
    srv_data = resp2.json()
    if len(srv_data["transitions"]) != 1:
        print(f"FAIL: expected 1 transition for srv-1, got {len(srv_data['transitions'])}")
        sys.exit(1)
    if srv_data["transitions"][0]["from_tier"] != "CRITICAL":
        print(f"FAIL: expected from_tier CRITICAL, got {srv_data['transitions'][0]['from_tier']}")
        sys.exit(1)

    # Test 3: 404 for unknown server
    resp3 = client.get("/api/risk/tier-transitions/servers/unknown-srv?period_days=7")
    if resp3.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp3.status_code}")
        sys.exit(1)

    # Test 4: tier distribution
    resp4 = client.get("/api/risk/tier-transitions/tier-distribution?period_days=7")
    if resp4.status_code != 200:
        print(f"FAIL: distribution endpoint returned {resp4.status_code}")
        sys.exit(1)
    dist_data = resp4.json()
    if not isinstance(dist_data, list):
        print("FAIL: distribution should return a list")
        sys.exit(1)

    print("PASS")
