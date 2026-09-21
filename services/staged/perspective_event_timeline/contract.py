"""
services.staged.perspective_event_timeline.contract
"""

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must not be mocked)
from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent, Base  # Base for test DB creation


router = APIRouter(prefix="/api")


# ---------- Pydantic schemas ----------
class EventItem(BaseModel):
    id: int
    server_id: int
    server_name: str
    change_type: str
    old_tier: str | None = None
    new_tier: str | None = None
    created_at: datetime
    seen: bool

    class Config:
        orm_mode = True


class TimelineResponse(BaseModel):
    perspective_id: int
    days: int
    events: List[EventItem] = Field(default_factory=list)


# ---------- Endpoint ----------
@router.get(
    "/perspectives/{perspective_id}/timeline",
    response_model=TimelineResponse,
    name="perspective_event_timeline",
)
def get_timeline(
    perspective_id: int,
    days: int = Query(7, ge=1),
    session: Session = Depends(get_session),
) -> TimelineResponse:
    """Return timeline events for a perspective."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        session.query(PerspectiveEvent, McpServerRegistry.name)
        .join(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.server_id,
        )
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.created_at >= cutoff,
        )
        .order_by(PerspectiveEvent.created_at.desc())
        .all()
    )

    events = [
        EventItem(
            id=ev.id,
            server_id=ev.server_id,
            server_name=server_name,
            change_type=ev.change_type,
            old_tier=ev.old_tier,
            new_tier=ev.new_tier,
            created_at=ev.created_at,
            seen=ev.seen,
        )
        for ev, server_name in rows
    ]

    return TimelineResponse(
        perspective_id=perspective_id,
        days=days,
        events=events,
    )


# ---------- Self‑test ----------
if __name__ == "__main__":
    # Build a minimal FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------
    # In‑memory SQLite setup (overrides the real DB dependency)
    # ------------------------------------------------------------------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    # Create tables for the imported models
    Base.metadata.create_all(engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    with TestingSessionLocal() as db:
        # Servers
        srv1 = McpServerRegistry(
            server_id=1,
            name="ServerOne",
            confidence=0.9,
            description="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta="{}",
            registry_source="test",
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://example.com/1",
            verdict="clean",
            verdict_reasoning="",
        )
        srv2 = McpServerRegistry(
            server_id=2,
            name="ServerTwo",
            confidence=0.8,
            description="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta="{}",
            registry_source="test",
            risk_tier="medium",
            scan_count=1,
            trust_score=0.7,
            url="http://example.com/2",
            verdict="clean",
            verdict_reasoning="",
        )
        db.add_all([srv1, srv2])
        db.flush()  # obtain PKs if needed

        now = datetime.utcnow()
        ev1 = PerspectiveEvent(
            id=1,
            perspective_id=1,
            server_id=1,
            change_type="tier_change",
            old_tier="low",
            new_tier="medium",
            seen=False,
            created_at=now - timedelta(days=1),
        )
        ev2 = PerspectiveEvent(
            id=2,
            perspective_id=1,
            server_id=2,
            change_type="tier_change",
            old_tier="medium",
            new_tier="high",
            seen=True,
            created_at=now,
        )
        ev3 = PerspectiveEvent(
            id=3,
            perspective_id=1,
            server_id=1,
            change_type="tier_change",
            old_tier="medium",
            new_tier="high",
            seen=False,
            created_at=now - timedelta(days=5),
        )
        db.add_all([ev1, ev2, ev3])
        db.commit()

    # ------------------------------------------------------------------
    # Run acceptance test
    # ------------------------------------------------------------------
    client = TestClient(app)

    resp = client.get("/api/perspectives/1/timeline?days=2")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["perspective_id"] == 1
    assert data["days"] == 2
    assert isinstance(data["events"], list)
    assert len(data["events"]) >= 2, "Expected at least 2 events"
    print("PASS")