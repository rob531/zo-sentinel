from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import PerspectiveEvent
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/perspectives")

class PerspectiveEventResponse(BaseModel):
    id: int
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: str

class PerspectiveEventsResponse(BaseModel):
    events: List[PerspectiveEventResponse]

@router.get("/{perspective_id}/events", response_model=PerspectiveEventsResponse)
async def get_perspective_events(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> PerspectiveEventsResponse:
    events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).all()

    if not events:
        raise HTTPException(status_code=404, detail="No events found for this perspective")

    return PerspectiveEventsResponse(
        events=[
            PerspectiveEventResponse(
                id=event.id,
                change_type=event.change_type,
                old_tier=event.old_tier,
                new_tier=event.new_tier,
                seen=event.seen,
                created_at=str(event.created_at)
            ) for event in events
        ]
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create test app with dependency overrides
    test_app = FastAPI()
    test_app.include_router(router)

    async def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    client = TestClient(test_app)
    with SessionLocal() as session:
        # Create a test perspective (id=1)
        from app.models import Perspective
        perspective = Perspective(
            id=1,
            name="Test Perspective",
            description="Test Description",
            org_id=1,
            created_by=1,
            facet_filters={}
        )
        session.add(perspective)

        # Create test events
        from datetime import datetime
        events = [
            PerspectiveEvent(
                perspective_id=1,
                change_type="tier_change",
                old_tier="LOW",
                new_tier="MEDIUM",
                seen=True,
                created_at=datetime.now(),
                server_id=1
            ),
            PerspectiveEvent(
                perspective_id=1,
                change_type="tier_change",
                old_tier="MEDIUM",
                new_tier="HIGH",
                seen=False,
                created_at=datetime.now(),
                server_id=2
            )
        ]
        session.add_all(events)
        session.commit()

    # Test the endpoint
    response = client.get("/api/perspectives/1/events")
    assert response.status_code == 200
    assert len(response.json()["events"]) == 2
    print("PASS")