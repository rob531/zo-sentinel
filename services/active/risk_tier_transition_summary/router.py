# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Transition Summary Service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, cast, Date, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["risk_tier_transition_summary"])


class TransitionCountEntry(BaseModel):
    from_tier: str
    to_tier: str
    count: int


class TierDistributionEntry(BaseModel):
    tier: str
    count: int


class SummaryResponse(BaseModel):
    period_days: int
    total_servers: int
    servers_with_transitions: int
    total_transitions: int
    tier_distribution: list[TierDistributionEntry]
    transition_counts: list[TransitionCountEntry]
    as_of: str


@router.get("/risk/tier-transition-summary", response_model=SummaryResponse)
def get_risk_tier_transition_summary(
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """
    Return a summary of risk tier transitions over the specified period.

    Includes:
    - Total servers in the registry
    - Number of servers that had at least one tier change
    - Total tier transitions
    - Current tier distribution
    - Transition counts by from_tier -> to_tier
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    # Current tier distribution from registry
    tier_dist_q = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    tier_distribution = [
        TierDistributionEntry(
            tier=(row.risk_tier or "UNKNOWN"),
            count=row.count,
        )
        for row in tier_dist_q
    ]

    total_servers = sum(e.count for e in tier_distribution)

    # Transitions from PerspectiveEvent (change_type, old_tier, new_tier)
    event_transitions = (
        db.query(
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            func.count(PerspectiveEvent.id).label("count"),
        )
        .filter(PerspectiveEvent.created_at >= cutoff)
        .filter(PerspectiveEvent.change_type.in_(["tier_change", "tier_upgrade", "tier_downgrade"]))
        .filter(PerspectiveEvent.old_tier.isnot(None))
        .filter(PerspectiveEvent.new_tier.isnot(None))
        .filter(PerspectiveEvent.old_tier != PerspectiveEvent.new_tier)
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .all()
    )

    # Transitions from axis scores (lag-based)
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

    current_tiers = (
        db.query(
            daily_latest.c.server_id,
            daily_latest.c.day,
            daily_latest.c.label,
        )
        .filter(daily_latest.c.rn == 1)
        .subquery()
    )

    with_prev = (
        db.query(
            current_tiers.c.server_id,
            current_tiers.c.day,
            current_tiers.c.label.label("curr_label"),
            func.lag(current_tiers.c.label, 1)
            .over(partition_by=current_tiers.c.server_id, order_by=current_tiers.c.day)
            .label("prev_label"),
        )
        .subquery()
    )

    score_transitions = (
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

    # Combine transition counts from both sources
    transition_map: dict[tuple[str, str], int] = {}
    for row in event_transitions:
        key = (row.old_tier or "UNKNOWN", row.new_tier or "UNKNOWN")
        transition_map[key] = transition_map.get(key, 0) + row.count
    for row in score_transitions:
        key = (row.prev_label or "UNKNOWN", row.curr_label or "UNKNOWN")
        transition_map[key] = transition_map.get(key, 0) + row.count

    transition_counts = [
        TransitionCountEntry(from_tier=k[0], to_tier=k[1], count=v)
        for k, v in sorted(transition_map.items())
    ]

    total_transitions = sum(e.count for e in transition_counts)

    # Count servers with at least one transition
    servers_with_transitions = len(transition_map)

    return SummaryResponse(
        period_days=period_days,
        total_servers=total_servers,
        servers_with_transitions=servers_with_transitions,
        total_transitions=total_transitions,
        tier_distribution=tier_distribution,
        transition_counts=transition_counts,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    import os as _os
    _svc = _os.environ.get("ZO_SENTINEL_ROOT")
    if _svc:
        sys.path.insert(0, _svc)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestSessionLocal() as db:
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        # Create test servers with different tiers
        servers = [
            McpServerRegistry(server_id="srv-1", name="Server 1", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv-2", name="Server 2", risk_tier="MEDIUM"),
            McpServerRegistry(server_id="srv-3", name="Server 3", risk_tier="CRITICAL"),
            McpServerRegistry(server_id="srv-4", name="Server 4", risk_tier="LOW"),
        ]
        db.add_all(servers)
        db.flush()

        # PerspectiveEvent transitions
        db.add(PerspectiveEvent(
            perspective_id="persp-1",
            server_id="srv-1",
            change_type="tier_change",
            old_tier="CRITICAL",
            new_tier="HIGH",
            seen=True,
            created_at=yesterday,
        ))
        db.add(PerspectiveEvent(
            perspective_id="persp-1",
            server_id="srv-2",
            change_type="tier_downgrade",
            old_tier="HIGH",
            new_tier="MEDIUM",
            seen=True,
            created_at=yesterday,
        ))

        # Axis score transitions (id is BigInteger autoincrement — SQLite needs explicit id)
        db.add(McpLlmAxisScore(
            id=1,
            server_id="srv-3",
            axis_name="overall_risk",
            label="CRITICAL",
            p_top=0.85,
            scored_at=two_days_ago,
            model_version="v1",
        ))
        db.add(McpLlmAxisScore(
            id=2,
            server_id="srv-3",
            axis_name="overall_risk",
            label="HIGH",
            p_top=0.65,
            scored_at=yesterday,
            model_version="v1",
        ))

        db.add(McpLlmAxisScore(
            id=3,
            server_id="srv-4",
            axis_name="overall_risk",
            label="MEDIUM",
            p_top=0.50,
            scored_at=two_days_ago,
            model_version="v1",
        ))
        db.add(McpLlmAxisScore(
            id=4,
            server_id="srv-4",
            axis_name="overall_risk",
            label="LOW",
            p_top=0.25,
            scored_at=yesterday,
            model_version="v1",
        ))

        db.commit()

    client = TestClient(app)

    resp = client.get("/api/risk/tier-transition-summary?period_days=7")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["total_servers"] == 4, f"Expected 4 servers, got {data['total_servers']}"
    assert data["total_transitions"] == 4, f"Expected 4 transitions, got {data['total_transitions']}"
    assert data["servers_with_transitions"] == 4, f"Expected 4 servers with transitions, got {data['servers_with_transitions']}"
    assert len(data["transition_counts"]) == 4, f"Expected 4 transition counts, got {len(data['transition_counts'])}"
    assert len(data["tier_distribution"]) == 4, f"Expected 4 tier distribution entries, got {len(data['tier_distribution'])}"

    # Verify specific transition
    transition_map = {(t["from_tier"], t["to_tier"]): t["count"] for t in data["transition_counts"]}
    assert transition_map.get(("CRITICAL", "HIGH")) == 2, f"Expected 2 CRITICAL->HIGH, got {transition_map.get(('CRITICAL', 'HIGH'))}"

    print("PASS")
    sys.exit(0)
