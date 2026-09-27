# deps: fastapi, pydantic, sqlalchemy
"""
Server Risk Tier Transition API.

Tracks how individual MCP servers move between risk tiers over time, based on
the overall_risk axis label in mcp_llm_axis_scores. Provides:
  - Aggregate transition matrix (from_tier -> to_tier counts)
  - Per-server transition history
  - Tier distribution snapshots over time
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import cast, Date, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_tier_transition_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

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


class TierCountAtPoint(BaseModel):
    tier: str
    count: int
    pct: float


class TierDistributionResponse(BaseModel):
    date: str
    distribution: List[TierCountAtPoint]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tier_from_label(label: Optional[str]) -> str:
    """Normalize a score label to a tier string."""
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
    "/risk/tier-transitions/matrix",
    response_model=TransitionMatrixResponse,
    summary="Aggregate risk tier transition matrix",
    responses={500: {"description": "Internal error"}},
)
def get_transition_matrix(
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TransitionMatrixResponse:
    """
    Counts all tier-to-tier transitions across every scored server in the
    window. Each row in the matrix is a from_tier -> to_tier pair with
    how many server-days showed that exact change.
    """
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    # One row per server per day: the latest overall_risk label that day
    daily = (
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

    latest = db.query(
        daily.c.server_id,
        daily.c.day,
        daily.c.label,
    ).filter(daily.c.rn == 1).subquery()

    # Lag within each server ordered by day → previous tier
    with_prev = db.query(
        latest.c.server_id,
        latest.c.day,
        latest.c.label.label("curr_label"),
        func.lag(latest.c.label, 1)
        .over(partition_by=latest.c.server_id, order_by=latest.c.day)
        .label("prev_label"),
    ).subquery()

    # Count transitions where tier actually changed
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
        if row.count and row.count > 0
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
    summary="Transition history for a specific server",
    responses={404: {"description": "Server not found"}},
)
def get_server_transition_history(
    server_id: str,
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerTransitionHistory:
    """Lists every tier change for one server over the given period."""
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
    summary="Risk tier distribution over time",
)
def get_tier_distribution(
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> List[TierDistributionResponse]:
    """One snapshot per day showing how many servers sat in each tier."""
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    daily = (
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

    latest = db.query(
        daily.c.day,
        daily.c.label,
    ).filter(daily.c.rn == 1).subquery()

    tier_counts = (
        db.query(
            latest.c.day,
            latest.c.label,
            func.count().label("count"),
        )
        .group_by(latest.c.day, latest.c.label)
        .order_by(latest.c.day)
        .all()
    )

    by_day: dict = {}
    for row in tier_counts:
        day_str = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
        by_day.setdefault(day_str, []).append(
            {"tier": _tier_from_label(row.label), "count": row.count}
        )

    result: List[TierDistributionResponse] = []
    for day_str, tiers in sorted(by_day.items()):
        total = sum(t["count"] for t in tiers)
        result.append(
            TierDistributionResponse(
                date=day_str,
                distribution=[
                    TierCountAtPoint(
                        tier=t["tier"],
                        count=t["count"],
                        pct=round(t["count"] / total * 100, 2) if total > 0 else 0.0,
                    )
                    for t in tiers
                ],
            )
        )

    return result


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
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)

    with TestSessionLocal() as sess:
        servers = [
            McpServerRegistry(
                server_id=f"srv-{i}", name=f"Server {i}", risk_tier="HIGH"
            )
            for i in range(1, 5)
        ]
        sess.add_all(servers)
        sess.commit()

        # srv-1: CRITICAL -> HIGH  (one transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-1", axis_name="overall_risk", label="CRITICAL",
            p_top=0.85, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-1", axis_name="overall_risk", label="HIGH",
            p_top=0.65, model_version="v1", scored_at=yesterday,
        ))

        # srv-2: HIGH -> MEDIUM  (one transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-2", axis_name="overall_risk", label="HIGH",
            p_top=0.70, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-2", axis_name="overall_risk", label="MEDIUM",
            p_top=0.45, model_version="v1", scored_at=yesterday,
        ))

        # srv-3: MEDIUM -> MEDIUM  (no transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-3", axis_name="overall_risk", label="MEDIUM",
            p_top=0.50, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-3", axis_name="overall_risk", label="MEDIUM",
            p_top=0.48, model_version="v1", scored_at=yesterday,
        ))

        # srv-4: LOW -> CRITICAL  (one transition)
        sess.add(McpLlmAxisScore(
            server_id="srv-4", axis_name="overall_risk", label="LOW",
            p_top=0.15, model_version="v1", scored_at=two_days_ago,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-4", axis_name="overall_risk", label="CRITICAL",
            p_top=0.88, model_version="v1", scored_at=yesterday,
        ))

        sess.commit()

    client = TestClient(test_app)

    # Test 1: transition matrix
    resp = client.get("/api/risk/tier-transitions/matrix?period_days=7")
    if resp.status_code != 200:
        print(f"FAIL: matrix returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["total_transitions"] != 3:
        print(f"FAIL: expected 3 total transitions, got {data['total_transitions']}")
        sys.exit(1)
    if len(data["matrix"]) != 3:
        print(f"FAIL: expected 3 matrix rows, got {len(data['matrix'])}")
        sys.exit(1)

    # Test 2: per-server transition history — CRITICAL -> HIGH
    resp2 = client.get("/api/risk/tier-transitions/servers/srv-1?period_days=7")
    if resp2.status_code != 200:
        print(f"FAIL: server history returned {resp2.status_code}")
        sys.exit(1)
    srv_data = resp2.json()
    if len(srv_data["transitions"]) != 1:
        print(f"FAIL: srv-1 expected 1 transition, got {len(srv_data['transitions'])}")
        sys.exit(1)
    if srv_data["transitions"][0]["from_tier"] != "CRITICAL":
        print(f"FAIL: expected from_tier CRITICAL, got {srv_data['transitions'][0]['from_tier']}")
        sys.exit(1)

    # Test 3: server with no transitions
    resp3 = client.get("/api/risk/tier-transitions/servers/srv-3?period_days=7")
    if resp3.status_code != 200:
        print(f"FAIL: srv-3 returned {resp3.status_code}")
        sys.exit(1)
    if len(resp3.json()["transitions"]) != 0:
        print("FAIL: srv-3 should have 0 transitions")
        sys.exit(1)

    # Test 4: 404 for unknown server
    resp4 = client.get("/api/risk/tier-transitions/servers/unknown-srv?period_days=7")
    if resp4.status_code != 404:
        print(f"FAIL: expected 404, got {resp4.status_code}")
        sys.exit(1)

    # Test 5: tier distribution
    resp5 = client.get("/api/risk/tier-transitions/tier-distribution?period_days=7")
    if resp5.status_code != 200:
        print(f"FAIL: distribution returned {resp5.status_code}")
        sys.exit(1)
    dist_data = resp5.json()
    if not isinstance(dist_data, list) or len(dist_data) == 0:
        print("FAIL: distribution should be a non-empty list")
        sys.exit(1)

    print("PASS")
