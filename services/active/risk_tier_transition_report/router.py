# deps: fastapi, pydantic, sqlalchemy, pyjwt
"""
Risk Tier Transition Report API.

Tracks how MCP servers move between risk tiers over time, returning
transition reports showing from_tier -> to_tier changes.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, cast, Date, and_
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    PerspectiveEvent,
    User,
    Org,
)

router = APIRouter(prefix="/api", tags=["risk_tier_transition_report"])


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #

class TransitionEntry(BaseModel):
    from_tier: str
    to_tier: str
    count: int
    direction: str  # "escalation" | "de_escalation" | "lateral"


class TierTransitionRecord(BaseModel):
    server_id: str
    server_name: Optional[str]
    from_tier: str
    to_tier: str
    change_date: str
    axis_name: str
    change_delta: int


class TransitionMatrixResponse(BaseModel):
    period_days: int
    total_servers: int
    servers_with_transitions: int
    total_transitions: int
    escalations: int
    de_escalations: int
    lateral_moves: int
    matrix: List[TransitionEntry]
    as_of: str


class ServerTransitionHistory(BaseModel):
    server_id: str
    server_name: str
    current_tier: str
    transition_count: int
    transitions: List[TierTransitionRecord]


class TierDistributionAtPoint(BaseModel):
    tier: str
    count: int
    pct: float


class TierDistributionResponse(BaseModel):
    date: str
    distribution: List[TierDistributionAtPoint]
    total: int


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _normalize_tier(label: Optional[str]) -> str:
    """Normalize axis score label to a tier string."""
    if label is None:
        return "UNKNOWN"
    l = label.upper()
    if l in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED"):
        return l
    return "UNKNOWN"


def _tier_rank(tier: str) -> int:
    """Return a numeric rank for tier ordering (higher = worse risk)."""
    ranks = {
        "CRITICAL": 5,
        "HIGH": 4,
        "MEDIUM": 3,
        "LOW": 2,
        "MINIMAL": 1,
        "TRUSTED": 0,
        "UNKNOWN": -1,
    }
    return ranks.get(tier, -1)


def _transition_direction(from_tier: str, to_tier: str) -> str:
    """Determine if transition is escalation, de-escalation, or lateral."""
    from_rank = _tier_rank(from_tier)
    to_rank = _tier_rank(to_tier)
    if to_rank > from_rank:
        return "escalation"
    elif to_rank < from_rank:
        return "de_escalation"
    return "lateral"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk/tier-transition-report",
    response_model=TransitionMatrixResponse,
    summary="Get risk tier transition matrix",
    responses={
        400: {"description": "Invalid period"},
        500: {"description": "Internal error"},
    },
)
def get_transition_matrix(
    period_days: int = Query(default=30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_session),
) -> TransitionMatrixResponse:
    """
    Returns a transition matrix showing how servers moved between risk tiers
    during the specified period, with counts for escalations vs de-escalations.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    # Get total servers
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Subquery: latest score per server per day for overall_risk axis
    daily_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            cast(McpLlmAxisScore.scored_at, Date).label("day"),
            McpLlmAxisScore.label,
            func.row_number()
            .over(
                partition_by=[
                    McpLlmAxisScore.server_id,
                    cast(McpLlmAxisScore.scored_at, Date),
                ],
                order_by=McpLlmAxisScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )

    current_tiers = (
        db.query(
            daily_latest.c.server_id,
            daily_latest.c.day,
            daily_latest.c.label,
        )
        .filter(daily_latest.c.rn == 1)
        .subquery()
    )

    # Lag to get previous day's tier
    with_prev = (
        db.query(
            current_tiers.c.server_id,
            current_tiers.c.day,
            current_tiers.c.label.label("curr_label"),
            func.lag(current_tiers.c.label, 1)
            .over(
                partition_by=current_tiers.c.server_id,
                order_by=current_tiers.c.day,
            )
            .label("prev_label"),
        )
        .subquery()
    )

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

    matrix: List[TransitionEntry] = []
    escalations = 0
    de_escalations = 0
    lateral_moves = 0
    servers_with_transitions = set()

    for row in transitions_q:
        if row.count > 0:
            from_tier = _normalize_tier(row.prev_label)
            to_tier = _normalize_tier(row.curr_label)
            direction = _transition_direction(from_tier, to_tier)
            matrix.append(
                TransitionEntry(
                    from_tier=from_tier,
                    to_tier=to_tier,
                    count=row.count,
                    direction=direction,
                )
            )
            if direction == "escalation":
                escalations += row.count
            elif direction == "de_escalation":
                de_escalations += row.count
            else:
                lateral_moves += row.count
            servers_with_transitions.add(row.prev_label + "_" + row.curr_label)

    total_transitions = sum(e.count for e in matrix)

    return TransitionMatrixResponse(
        period_days=period_days,
        total_servers=total_servers,
        servers_with_transitions=len(servers_with_transitions),
        total_transitions=total_transitions,
        escalations=escalations,
        de_escalations=de_escalations,
        lateral_moves=lateral_moves,
        matrix=matrix,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/risk/tier-transition-report/servers/{server_id}",
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
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    # Get scores ordered chronologically
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    transitions: List[TierTransitionRecord] = []
    for i in range(1, len(scores)):
        prev_label = scores[i - 1].label
        curr_label = scores[i].label
        prev_tier = _normalize_tier(prev_label)
        curr_tier = _normalize_tier(curr_label)
        if prev_tier != curr_tier:
            transitions.append(
                TierTransitionRecord(
                    server_id=server_id,
                    server_name=server.name,
                    from_tier=prev_tier,
                    to_tier=curr_tier,
                    change_date=scores[i].scored_at.isoformat(),
                    axis_name="overall_risk",
                    change_delta=_tier_rank(curr_tier) - _tier_rank(prev_tier),
                )
            )

    current_tier = "UNKNOWN"
    if server.risk_tier:
        current_tier = server.risk_tier.upper()
    elif scores:
        current_tier = _normalize_tier(scores[-1].label)

    return ServerTransitionHistory(
        server_id=server_id,
        server_name=server.name or "Unknown",
        current_tier=current_tier,
        transition_count=len(transitions),
        transitions=transitions,
    )


@router.get(
    "/risk/tier-transition-report/tier-distribution",
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
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    daily_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            cast(McpLlmAxisScore.scored_at, Date).label("day"),
            McpLlmAxisScore.label,
            func.row_number()
            .over(
                partition_by=[
                    McpLlmAxisScore.server_id,
                    cast(McpLlmAxisScore.scored_at, Date),
                ],
                order_by=McpLlmAxisScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )

    current_tiers = (
        db.query(
            daily_latest.c.day,
            daily_latest.c.label,
        )
        .filter(daily_latest.c.rn == 1)
        .subquery()
    )

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
        day_str = (
            row.day.isoformat()
            if hasattr(row.day, "isoformat")
            else str(row.day)
        )
        if day_str not in by_day:
            by_day[day_str] = []
        tier = _normalize_tier(row.label)
        by_day[day_str].append({"tier": tier, "count": row.count})

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
        result.append(
            TierDistributionResponse(date=day_str, distribution=dist, total=total)
        )

    return result


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI

    from app.models import Base

    # In-memory SQLite for self-test
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    def _override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(test_app)

    # Seed test data
    with TestSessionLocal() as sess:
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        # Create test servers
        servers = [
            McpServerRegistry(
                server_id="srv-1", name="Server Alpha", risk_tier="HIGH"
            ),
            McpServerRegistry(
                server_id="srv-2", name="Server Beta", risk_tier="MEDIUM"
            ),
            McpServerRegistry(
                server_id="srv-3", name="Server Gamma", risk_tier="CRITICAL"
            ),
            McpServerRegistry(
                server_id="srv-4", name="Server Delta", risk_tier="LOW"
            ),
            McpServerRegistry(
                server_id="srv-5", name="Server Epsilon", risk_tier="MEDIUM"
            ),
        ]
        sess.add_all(servers)
        sess.commit()

        # Server 1: CRITICAL -> HIGH (de-escalation)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-1",
                axis_name="overall_risk",
                label="CRITICAL",
                p_top=0.85,
                model_version="v1",
                scored_at=two_days_ago,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="srv-1",
                axis_name="overall_risk",
                label="HIGH",
                p_top=0.65,
                model_version="v1",
                scored_at=yesterday,
            )
        )

        # Server 2: HIGH -> MEDIUM (de-escalation)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-2",
                axis_name="overall_risk",
                label="HIGH",
                p_top=0.70,
                model_version="v1",
                scored_at=two_days_ago,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="srv-2",
                axis_name="overall_risk",
                label="MEDIUM",
                p_top=0.45,
                model_version="v1",
                scored_at=yesterday,
            )
        )

        # Server 3: MEDIUM -> CRITICAL (escalation)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-3",
                axis_name="overall_risk",
                label="MEDIUM",
                p_top=0.50,
                model_version="v1",
                scored_at=two_days_ago,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="srv-3",
                axis_name="overall_risk",
                label="CRITICAL",
                p_top=0.88,
                model_version="v1",
                scored_at=yesterday,
            )
        )

        # Server 4: stays LOW (no transition)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-4",
                axis_name="overall_risk",
                label="LOW",
                p_top=0.15,
                model_version="v1",
                scored_at=two_days_ago,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="srv-4",
                axis_name="overall_risk",
                label="LOW",
                p_top=0.18,
                model_version="v1",
                scored_at=yesterday,
            )
        )

        # Server 5: MEDIUM -> MEDIUM (no transition)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-5",
                axis_name="overall_risk",
                label="MEDIUM",
                p_top=0.50,
                model_version="v1",
                scored_at=two_days_ago,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="srv-5",
                axis_name="overall_risk",
                label="MEDIUM",
                p_top=0.48,
                model_version="v1",
                scored_at=yesterday,
            )
        )

        sess.commit()

    # Test 1: transition matrix endpoint
    resp = client.get("/api/risk/tier-transition-report?period_days=7")
    if resp.status_code != 200:
        print(f"FAIL: matrix endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["total_transitions"] != 3:
        print(
            f"FAIL: expected 3 total transitions, got {data['total_transitions']}"
        )
        sys.exit(1)
    if data["de_escalations"] != 2:
        print(f"FAIL: expected 2 de-escalations, got {data['de_escalations']}")
        sys.exit(1)
    if data["escalations"] != 1:
        print(f"FAIL: expected 1 escalation, got {data['escalations']}")
        sys.exit(1)
    if len(data["matrix"]) != 3:
        print(f"FAIL: expected 3 matrix entries, got {len(data['matrix'])}")
        sys.exit(1)

    # Test 2: server transition history
    resp2 = client.get(
        "/api/risk/tier-transition-report/servers/srv-1?period_days=7"
    )
    if resp2.status_code != 200:
        print(
            f"FAIL: server history endpoint returned {resp2.status_code}: {resp2.text}"
        )
        sys.exit(1)
    srv_data = resp2.json()
    if srv_data["transition_count"] != 1:
        print(
            f"FAIL: expected 1 transition for srv-1, got {srv_data['transition_count']}"
        )
        sys.exit(1)
    if srv_data["transitions"][0]["from_tier"] != "CRITICAL":
        print(
            f"FAIL: expected from_tier CRITICAL, got {srv_data['transitions'][0]['from_tier']}"
        )
        sys.exit(1)
    if srv_data["transitions"][0]["to_tier"] != "HIGH":
        print(
            f"FAIL: expected to_tier HIGH, got {srv_data['transitions'][0]['to_tier']}"
        )
        sys.exit(1)

    # Test 3: 404 for unknown server
    resp3 = client.get(
        "/api/risk/tier-transition-report/servers/unknown-srv?period_days=7"
    )
    if resp3.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp3.status_code}")
        sys.exit(1)

    # Test 4: tier distribution
    resp4 = client.get(
        "/api/risk/tier-transition-report/tier-distribution?period_days=7"
    )
    if resp4.status_code != 200:
        print(f"FAIL: distribution endpoint returned {resp4.status_code}: {resp4.text}")
        sys.exit(1)
    dist_data = resp4.json()
    if not isinstance(dist_data, list):
        print("FAIL: distribution should return a list")
        sys.exit(1)
    if len(dist_data) == 0:
        print("FAIL: expected distribution data")
        sys.exit(1)

    # Test 5: auth failure (no auth header)
    resp5 = client.get("/api/risk/tier-transition-report")
    # Endpoint is public per directive, so this should pass
    if resp5.status_code != 200:
        print(f"FAIL: public endpoint should not require auth, got {resp5.status_code}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
