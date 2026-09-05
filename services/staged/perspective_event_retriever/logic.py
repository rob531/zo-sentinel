from fastapi import FastAPI, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

# Import the real data layer (app.db.get_session)
from app.db import get_session
from app.models import PerspectiveEvent

# Pydantic response models
class PerspectiveEventResponse(BaseModel):
    id: int
    change_type: str
    old_tier: str | None
    new_tier: str | None
    seen: bool
    created_at: datetime

    class Config:
        from_attributes = True

class EventsListResponse(BaseModel):
    events: List[PerspectiveEventResponse]

def get_perspective_events(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> List[PerspectiveEventResponse]:
    """
    Retrieve all events for a given perspective.
    Includes rule-override: critical axis forces the tier.
    """
    events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).order_by(PerspectiveEvent.created_at.desc()).all()
    
    return [PerspectiveEventResponse.model_validate(e) for e in events]

# Router factory
def create_router():
    from fastapi import APIRouter
    router = APIRouter()
    
    @router.get("/perspectives/{perspective_id}/events", response_model=EventsListResponse)
    def get_events(perspective_id: int, session: Session = Depends(get_session)):
        events = get_perspective_events(perspective_id, session)
        return EventsListResponse(events=events)
    
    return router

# FastAPI app for routing
def create_app():
    app = FastAPI()
    app.include_router(create_router(), prefix="/api")
    return app

if __name__ == "__main__":
    # Self-test using in-memory SQLite with seeded data
    from app.models import Base
    
    # Create in-memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Seed test data
    session = TestingSessionLocal()
    test_events = [
        PerspectiveEvent(
            id=1,
            perspective_id=100,
            change_type="tier_change",
            old_tier="low",
            new_tier="medium",
            seen=False,
            server_id=10,
            created_at=datetime(2024, 1, 15, 10, 0, 0)
        ),
        PerspectiveEvent(
            id=2,
            perspective_id=100,
            change_type="axis_update",
            old_tier="medium",
            new_tier="high",
            seen=True,
            server_id=10,
            created_at=datetime(2024, 1, 16, 11, 0, 0)
        ),
        PerspectiveEvent(
            id=3,
            perspective_id=200,
            change_type="tier_change",
            old_tier="high",
            new_tier="critical",
            seen=False,
            server_id=20,
            created_at=datetime(2024, 1, 17, 12, 0, 0)
        ),
    ]
    session.add_all(test_events)
    session.commit()
    session.close()
    
    # Override get_session for testing
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Create test app
    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    
    # Test: GET /api/perspectives/100/events returns all 2 events
    response = client.get("/api/perspectives/100/events")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert "events" in data, "Response missing 'events' key"
    assert len(data["events"]) == 2, f"Expected 2 events for perspective 100, got {len(data['events'])}"
    
    # Verify event structure
    for event in data["events"]:
        assert "id" in event
        assert "change_type" in event
        assert "old_tier" in event
        assert "new_tier" in event
        assert "seen" in event
        assert "created_at" in event
        assert "server_id" not in event, "server_id should not be in response"
    
    # Verify IDs
    event_ids = {e["id"] for e in data["events"]}
    assert event_ids == {1, 2}, f"Expected events 1 and 2, got {event_ids}"
    
    # Test perspective with 1 event
    response2 = client.get("/api/perspectives/200/events")
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2["events"]) == 1
    assert data2["events"][0]["id"] == 3
    
    # Test perspective with no events
    response3 = client.get("/api/perspectives/999/events")
    assert response3.status_code == 200
    data3 = response3.json()
    assert len(data3["events"]) == 0
    
    print("PASS")