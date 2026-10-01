# deps: fastapi, sqlalchemy, pydantic
"""Perspective Event Stats Service.

Aggregated statistics and summaries for perspective events.
Reads from perspective_events and perspective_snapshots via the app DB session.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot, Perspective

router = APIRouter(prefix="/api", tags=["perspective_event_stats"])


# --- Response models -------------------------------------------------------

class EventCountByType(BaseModel):
    change_type: str
    count: int


class TierChangeStat(BaseModel):
    transition: str  # "old_tier -> new_tier"
    count: int


class PerspectiveStats(BaseModel):
    perspective_id: str
    total_events: int
    unseen_count: int
    unique_servers: int
    by_change_type: List[EventCountByType]
    by_tier_change: List[TierChangeStat]
    first_event_at: Optional[datetime]
    latest_event_at: Optional[datetime]


class OverallEventStats(BaseModel):
    total_perspectives_with_events: int
    total_events: int
    total_unseen: int
    top_perspectives: List[PerspectiveStats]


# --- Endpoints ------------------------------------------------------------

@router.get(
    "/perspectives/{perspective_id}/stats",
    response_model=PerspectiveStats,
)
def get_perspective_stats(
    perspective_id: str,
    db: Session = Depends(get_session),
) -> PerspectiveStats:
    """Return aggregated statistics for a single perspective."""
    q = db.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    )

    total = q.count()

    unseen_count = (
        db.query(func.count(PerspectiveEvent.id))
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen == False,  # noqa: E712
        )
        .scalar() or 0
    )

    unique_servers = (
        db.query(func.count(distinct(PerspectiveEvent.server_id)))
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .scalar() or 0
    )

    by_type_rows = (
        db.query(
            PerspectiveEvent.change_type,
            func.count(PerspectiveEvent.id).label("cnt"),
        )
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .group_by(PerspectiveEvent.change_type)
        .all()
    )
    by_change_type = [
        EventCountByType(change_type=r.change_type, count=r.cnt)
        for r in by_type_rows
    ]

    by_tier_rows = (
        db.query(
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            func.count(PerspectiveEvent.id).label("cnt"),
        )
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.old_tier.isnot(None),
            PerspectiveEvent.new_tier.isnot(None),
        )
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .all()
    )
    by_tier_change = [
        TierChangeStat(
            transition=f"{r.old_tier} -> {r.new_tier}",
            count=r.cnt,
        )
        for r in by_tier_rows
    ]

    first_row = (
        db.query(PerspectiveEvent.created_at)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at)
        .first()
    )
    first_event_at = first_row[0] if first_row else None

    latest_row = (
        db.query(PerspectiveEvent.created_at)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .first()
    )
    latest_event_at = latest_row[0] if latest_row else None

    return PerspectiveStats(
        perspective_id=perspective_id,
        total_events=total,
        unseen_count=unseen_count,
        unique_servers=unique_servers,
        by_change_type=by_change_type,
        by_tier_change=by_tier_change,
        first_event_at=first_event_at,
        latest_event_at=latest_event_at,
    )


@router.get(
    "/perspectives/stats/overview",
    response_model=OverallEventStats,
)
def get_overall_event_stats(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_session),
) -> OverallEventStats:
    """Return overall event statistics across all perspectives."""
    perspective_ids = [
        r[0]
        for r in db.query(distinct(PerspectiveEvent.perspective_id)).all()
    ]

    total_events = db.query(func.count(PerspectiveEvent.id)).scalar() or 0
    total_unseen = (
        db.query(func.count(PerspectiveEvent.id))
        .filter(PerspectiveEvent.seen == False)  # noqa: E712
        .scalar() or 0
    )

    top_stats: List[PerspectiveStats] = []
    for pid in perspective_ids[:limit]:
        stats = get_perspective_stats(pid, db)
        top_stats.append(stats)

    return OverallEventStats(
        total_perspectives_with_events=len(perspective_ids),
        total_events=total_events,
        total_unseen=total_unseen,
        top_perspectives=top_stats,
    )


# --- Self-test ------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(router)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override

    with TestSession() as s:
        s.add_all([
            PerspectiveEvent(
                perspective_id="pid-stats-1",
                server_id="srv-a",
                change_type="entered",
                old_tier=None,
                new_tier="HIGH",
                seen=False,
                created_at=datetime(2024, 1, 1, 10, 0, 0),
            ),
            PerspectiveEvent(
                perspective_id="pid-stats-1",
                server_id="srv-b",
                change_type="tier_changed",
                old_tier="LOW",
                new_tier="HIGH",
                seen=False,
                created_at=datetime(2024, 1, 2, 11, 0, 0),
            ),
            PerspectiveEvent(
                perspective_id="pid-stats-1",
                server_id="srv-c",
                change_type="tier_changed",
                old_tier="HIGH",
                new_tier="LOW",
                seen=True,
                created_at=datetime(2024, 1, 3, 12, 0, 0),
            ),
            PerspectiveEvent(
                perspective_id="pid-stats-2",
                server_id="srv-d",
                change_type="left",
                old_tier="MEDIUM",
                new_tier=None,
                seen=False,
                created_at=datetime(2024, 1, 5, 8, 0, 0),
            ),
        ])
        s.commit()

    client = TestClient(app)

    # Test 1: single perspective stats
    resp = client.get("/api/perspectives/pid-stats-1/stats")
    if resp.status_code != 200:
        print(f"FAIL: stats returned {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if data["total_events"] != 3:
        print(f"FAIL: expected 3 total events, got {data['total_events']}")
        sys.exit(1)
    if data["unseen_count"] != 2:
        print(f"FAIL: expected 2 unseen, got {data['unseen_count']}")
        sys.exit(1)
    if data["unique_servers"] != 3:
        print(f"FAIL: expected 3 unique servers, got {data['unique_servers']}")
        sys.exit(1)

    # Test 2: change type breakdown
    by_type = {e["change_type"]: e["count"] for e in data["by_change_type"]}
    if by_type.get("entered", 0) != 1:
        print(f"FAIL: expected 1 entered, got {by_type}")
        sys.exit(1)
    if by_type.get("tier_changed", 0) != 2:
        print(f"FAIL: expected 2 tier_changed, got {by_type}")
        sys.exit(1)

    # Test 3: tier change breakdown
    by_tier = {e["transition"]: e["count"] for e in data["by_tier_change"]}
    if by_tier.get("LOW -> HIGH", 0) != 1:
        print(f"FAIL: expected 1 LOW->HIGH, got {by_tier}")
        sys.exit(1)
    if by_tier.get("HIGH -> LOW", 0) != 1:
        print(f"FAIL: expected 1 HIGH->LOW, got {by_tier}")
        sys.exit(1)

    # Test 4: overview endpoint
    resp2 = client.get("/api/perspectives/stats/overview?limit=5")
    if resp2.status_code != 200:
        print(f"FAIL: overview returned {resp2.status_code}")
        sys.exit(1)
    overview = resp2.json()
    if overview["total_events"] != 4:
        print(f"FAIL: expected 4 total events in overview, got {overview['total_events']}")
        sys.exit(1)
    if overview["total_perspectives_with_events"] != 2:
        print(f"FAIL: expected 2 perspectives, got {overview['total_perspectives_with_events']}")
        sys.exit(1)
    if len(overview["top_perspectives"]) != 2:
        print(f"FAIL: expected 2 top perspectives, got {len(overview['top_perspectives'])}")
        sys.exit(1)

    # Test 5: empty perspective
    resp3 = client.get("/api/perspectives/nonexistent/stats")
    if resp3.status_code != 200:
        print(f"FAIL: empty perspective should return 200, got {resp3.status_code}")
        sys.exit(1)
    empty_data = resp3.json()
    if empty_data["total_events"] != 0:
        print(f"FAIL: empty perspective should have 0 events")
        sys.exit(1)

    print("PASS")
