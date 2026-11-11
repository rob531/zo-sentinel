from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


class PerspectiveSeenRate(BaseModel):
    perspective_id: str
    total_events: int
    seen: int
    seen_rate: float
    by_change_type: Dict[str, int] = Field(default_factory=dict)
    transitions: Dict[str, int] = Field(default_factory=dict)


class EventSeenRateResponse(BaseModel):
    days: int
    perspectives: List[PerspectiveSeenRate]
    perspective_count: int


_TIER_RANKS = {
    "NONE": 0,
    "SAFE": 1,
    "LOW": 2,
    "LOW_RISK": 3,
    "MONITORED": 4,
    "STANDARD": 5,
    "MEDIUM": 6,
    "MEDIUM_RISK": 7,
    "ELEVATED": 8,
    "ENTERPRISE_CONTROLLED": 9,
    "HIGH": 10,
    "HIGH_RISK": 11,
    "HIGH_RISK_ISOLATED": 12,
    "CRITICAL": 13,
    "CRITICAL_RISK": 14,
}


def transition_label(old_tier: Any, new_tier: Any) -> str:
    """Return the stable label used for a tier-transition count."""
    old_label = "NONE" if old_tier is None else str(old_tier)
    new_label = "NONE" if new_tier is None else str(new_tier)
    return f"{old_label}->{new_label}"


def tier_change_kind(old_tier: Any, new_tier: Any) -> str:
    """Classify a tier change independently of the stored change_type value."""
    if old_tier is None and new_tier is not None:
        return "entered"
    if old_tier is not None and new_tier is None:
        return "de-escalated"
    if old_tier == new_tier:
        return "unchanged"

    old_key = str(old_tier or "NONE").upper()
    new_key = str(new_tier or "NONE").upper()
    old_rank = _TIER_RANKS.get(old_key)
    new_rank = _TIER_RANKS.get(new_key)

    if old_rank is None or new_rank is None:
        return "unchanged"
    return "escalated" if new_rank > old_rank else "de-escalated"


def compute_event_seen_rate(db: Session, days: int = 30) -> Dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.utcnow() - timedelta(days=days)
    statement = select(
        PerspectiveEvent.perspective_id,
        PerspectiveEvent.seen,
        PerspectiveEvent.change_type,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
    ).where(PerspectiveEvent.created_at >= cutoff)

    totals: Dict[str, int] = defaultdict(int)
    seen_counts: Dict[str, int] = defaultdict(int)
    change_types: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    transitions: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for perspective_id, seen, change_type, old_tier, new_tier in db.execute(statement):
        key = str(perspective_id) if perspective_id is not None else "unknown"
        totals[key] += 1
        if bool(seen):
            seen_counts[key] += 1

        change_key = str(change_type) if change_type is not None else "unknown"
        change_types[key][change_key] += 1
        transitions[key][transition_label(old_tier, new_tier)] += 1

    perspectives: List[Dict[str, Any]] = []
    for perspective_id in sorted(totals):
        total_events = totals[perspective_id]
        seen = seen_counts[perspective_id]
        perspectives.append(
            {
                "perspective_id": perspective_id,
                "total_events": total_events,
                "seen": seen,
                "seen_rate": seen / total_events if total_events else 0.0,
                "by_change_type": dict(sorted(change_types[perspective_id].items())),
                "transitions": dict(sorted(transitions[perspective_id].items())),
            }
        )

    return {
        "days": days,
        "perspectives": perspectives,
        "perspective_count": len(perspectives),
    }


get_event_seen_rate = compute_event_seen_rate
calculate_event_seen_rate = compute_event_seen_rate
get_perspective_event_seen_rate = compute_event_seen_rate


router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/event-seen-rate",
    response_model=EventSeenRateResponse,
)
def event_seen_rate_endpoint(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return compute_event_seen_rate(db, days)


app = FastAPI(title="Perspective Event Seen Rate API")
app.include_router(router)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(test_engine)
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with SessionLocal.begin() as session:
        session.execute(
            PerspectiveEvent.__table__.insert(),
            [
                {
                    "perspective_id": "p1",
                    "server_id": "s1",
                    "change_type": "tier_change",
                    "old_tier": "ENTERPRISE_CONTROLLED",
                    "new_tier": "HIGH_RISK_ISOLATED",
                    "seen": True,
                    "created_at": now,
                },
                {
                    "perspective_id": "p1",
                    "server_id": "s1",
                    "change_type": "seen",
                    "old_tier": "HIGH_RISK_ISOLATED",
                    "new_tier": "HIGH_RISK_ISOLATED",
                    "seen": True,
                    "created_at": now,
                },
                {
                    "perspective_id": "p1",
                    "server_id": "s1",
                    "change_type": "seen",
                    "old_tier": "HIGH_RISK_ISOLATED",
                    "new_tier": "HIGH_RISK_ISOLATED",
                    "seen": False,
                    "created_at": now,
                },
                {
                    "perspective_id": "p1",
                    "server_id": "s1",
                    "change_type": "seen",
                    "old_tier": "HIGH_RISK_ISOLATED",
                    "new_tier": "HIGH_RISK_ISOLATED",
                    "seen": False,
                    "created_at": now,
                },
            ],
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with SessionLocal() as session:
        result = compute_event_seen_rate(session, 30)

    perspective = result["perspectives"][0]
    assert result["perspective_count"] == 1
    assert perspective["total_events"] == 4
    assert perspective["seen"] == 2
    assert perspective["seen_rate"] == 0.5
    assert "ENTERPRISE_CONTROLLED->HIGH_RISK_ISOLATED" in perspective["transitions"]
    print("PASS")