# services/staged/perspective_events/router.py
from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_events"])


class PerspectiveEventResponse(BaseModel):
    perspective_id: int
    server_id: int
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PaginatedPerspectiveEventsResponse(BaseModel):
    items: List[PerspectiveEventResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


def _build_perspective_event_response(event: PerspectiveEvent) -> PerspectiveEventResponse:
    return PerspectiveEventResponse(
        perspective_id=event.perspective_id,
        server_id=event.server_id,
        change_type=event.change_type,
        old_tier=event.old_tier,
        new_tier=event.new_tier,
        seen=event.seen,
        created_at=event.created_at,
    )


def get_perspective_events(
    session: Session,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedPerspectiveEventsResponse:
    offset = (page - 1) * page_size

    total = session.query(PerspectiveEvent).count()

    events = (
        session.query(PerspectiveEvent)
        .order_by(PerspectiveEvent.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return PaginatedPerspectiveEventsResponse(
        items=[_build_perspective_event_response(e) for e in events],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/perspective-events", response_model=PaginatedPerspectiveEventsResponse)
def list_perspective_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> PaginatedPerspectiveEventsResponse:
    return get_perspective_events(session, page=page, page_size=page_size)


# Functions called by other services
def signal_handler(server_id: int, session: Session) -> List[PerspectiveEventResponse]:
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(100)
        .all()
    )
    return [_build_perspective_event_response(e) for e in events]


def get_aggregated_evidence(
    perspective_id: int, session: Session
) -> List[PerspectiveEventResponse]:
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(50)
        .all()
    )
    return [_build_perspective_event_response(e) for e in events]


def update_cve_summary(
    server_id: int, session: Session
) -> List[PerspectiveEventResponse]:
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .filter(PerspectiveEvent.change_type == "tier_change")
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(20)
        .all()
    )
    return [_build_perspective_event_response(e) for e in events]


def get_severity(
    server_id: int, session: Session
) -> Optional[PerspectiveEventResponse]:
    event = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .filter(PerspectiveEvent.change_type == "severity_change")
        .order_by(PerspectiveEvent.created_at.desc())
        .first()
    )
    if event:
        return _build_perspective_event_response(event)
    return None


def ensure_overview_table(session: Session) -> int:
    count = session.query(PerspectiveEvent).count()
    return count


def get_server_registry_facts(
    server_id: int, session: Session
) -> List[PerspectiveEventResponse]:
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(10)
        .all()
    )
    return [_build_perspective_event_response(e) for e in events]


def heartbeat_loop(session: Session) -> int:
    return session.query(PerspectiveEvent).filter(
        PerspectiveEvent.seen == True
    ).count()


def get_risk_history(
    server_id: int, days: int, session: Session
) -> List[PerspectiveEventResponse]:
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .filter(PerspectiveEvent.change_type == "tier_change")
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(days)
        .all()
    )
    return [_build_perspective_event_response(e) for e in events]


def get_current_risk_data(
    server_id: int, session: Session
) -> Optional[PerspectiveEventResponse]:
    event = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .filter(PerspectiveEvent.change_type == "tier_change")
        .order_by(PerspectiveEvent.created_at.desc())
        .first()
    )
    if event:
        return _build_perspective_event_response(event)
    return None


def get_transition_counts_by_day(
    days: int, session: Session
) -> dict:
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.created_at >= cutoff)
        .filter(PerspectiveEvent.change_type == "tier_change")
        .all()
    )

    counts = {}
    for event in events:
        date_key = event.created_at.date().isoformat()
        counts[date_key] = counts.get(date_key, 0) + 1

    return counts


def get_registry_source_freshness_report(session: Session) -> dict:
    total = session.query(PerspectiveEvent).count()
    seen = session.query(PerspectiveEvent).filter(PerspectiveEvent.seen == True).count()
    return {"total_events": total, "seen_events": seen, "freshness_ratio": seen / total if total > 0 else 0}


def get_server_tier(server_id: int, session: Session) -> Optional[str]:
    event = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .filter(PerspectiveEvent.change_type == "tier_change")
        .order_by(PerspectiveEvent.created_at.desc())
        .first()
    )
    if event:
        return event.new_tier
    return None


def get_latest_verdict(
    server_id: int, session: Session
) -> Optional[PerspectiveEventResponse]:
    event = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .first()
    )
    if event:
        return _build_perspective_event_response(event)
    return None


def get_server_signal_history(
    server_id: int, session: Session
) -> List[PerspectiveEventResponse]:
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(100)
        .all()
    )
    return [_build_perspective_event_response(e) for e in events]


def health(session: Session) -> dict:
    count = session.query(PerspectiveEvent).count()
    return {"status": "healthy", "event_count": count}


def _get_attestation_count(session: Session) -> int:
    return session.query(PerspectiveEvent).filter(
        PerspectiveEvent.change_type == "attestation"
    ).count()


def get_risk_distribution(session: Session) -> dict:
    events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.change_type == "tier_change"
    ).all()

    distribution = {}
    for event in events:
        tier = event.new_tier or "unknown"
        distribution[tier] = distribution.get(tier, 0) + 1

    return distribution


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "services/staged/perspective_events")

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    that_app = None

    from fastapi import FastAPI
    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    db = TestingSessionLocal()
    now = datetime.utcnow()

    event1 = PerspectiveEvent(
        perspective_id=1,
        server_id=100,
        change_type="tier_change",
        old_tier="low",
        new_tier="high",
        seen=True,
        created_at=now,
    )
    event2 = PerspectiveEvent(
        perspective_id=2,
        server_id=101,
        change_type="severity_change",
        old_tier="medium",
        new_tier="critical",
        seen=False,
        created_at=now,
    )
    event3 = PerspectiveEvent(
        perspective_id=3,
        server_id=100,
        change_type="tier_change",
        old_tier="high",
        new_tier="medium",
        seen=True,
        created_at=now,
    )

    db.add(event1)
    db.add(event2)
    db.add(event3)
    db.commit()
    db.close()

    client = TestClient(that_app)

    response = client.get("/api/perspective-events", params={"page": 1, "page_size": 10})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "items" in data, "Response missing 'items'"
    assert "total" in data, "Response missing 'total'"
    assert "page" in data, "Response missing 'page'"
    assert "page_size" in data, "Response missing 'page_size'"
    assert "total_pages" in data, "Response missing 'total_pages'"

    assert data["total"] == 3, f"Expected 3 events, got {data['total']}"
    assert len(data["items"]) == 3, f"Expected 3 items, got {len(data['items'])}"

    first_item = data["items"][0]
    required_fields = ["perspective_id", "server_id", "change_type", "old_tier", "new_tier", "seen", "created_at"]
    for field in required_fields:
        assert field in first_item, f"Missing field: {field}"

    assert data["items"][0]["server_id"] == 100, "First event should have server_id 100"
    assert data["items"][0]["change_type"] == "tier_change", "First event should be tier_change"

    print("PASS")