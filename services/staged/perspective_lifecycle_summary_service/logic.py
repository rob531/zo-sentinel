from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

def get_perspective_lifecycle_summary(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> dict:
    # Get the perspective
    perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Get the first and last event for this perspective
    first_event = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).order_by(PerspectiveEvent.created_at.asc()).first()

    last_event = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).order_by(PerspectiveEvent.created_at.desc()).first()

    # Count all events for this perspective
    event_count = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).count()

    # Determine last event type if there are events
    last_event_type = None
    if last_event:
        last_event_type = last_event.change_type

    return {
        "perspective_id": perspective_id,
        "creation_date": perspective.created_at,
        "last_updated": perspective.updated_at,
        "event_count": event_count,
        "last_event_type": last_event_type
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Create test app
    app = FastAPI()

    # Override get_session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test client
    client = TestClient(app)

    # Test data
    from app.models import Perspective, PerspectiveEvent
    from datetime import datetime, timedelta

    # Create test perspectives and events
    with SessionLocal() as session:
        # Perspective 1
        p1 = Perspective(
            id=1,
            name="Test Perspective 1",
            description="Test description 1",
            org_id=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by=1,
            facet_filters={}
        )
        session.add(p1)

        # Perspective 2
        p2 = Perspective(
            id=2,
            name="Test Perspective 2",
            description="Test description 2",
            org_id=1,
            created_at=datetime.now() - timedelta(days=1),
            updated_at=datetime.now() - timedelta(days=1),
            created_by=1,
            facet_filters={}
        )
        session.add(p2)

        # Events for perspective 1
        e1 = PerspectiveEvent(
            id=1,
            perspective_id=1,
            change_type="created",
            created_at=datetime.now() - timedelta(hours=2),
            server_id=1,
            seen=True
        )
        session.add(e1)

        e2 = PerspectiveEvent(
            id=2,
            perspective_id=1,
            change_type="updated",
            created_at=datetime.now() - timedelta(hours=1),
            server_id=1,
            seen=True
        )
        session.add(e2)

        # Events for perspective 2
        e3 = PerspectiveEvent(
            id=3,
            perspective_id=2,
            change_type="created",
            created_at=datetime.now() - timedelta(days=1, hours=3),
            server_id=1,
            seen=True
        )
        session.add(e3)

        session.commit()

    # Test the function
    result1 = get_perspective_lifecycle_summary(1, SessionLocal())
    result2 = get_perspective_lifecycle_summary(2, SessionLocal())

    # Assertions
    assert result1["perspective_id"] == 1
    assert result1["event_count"] == 2
    assert result1["last_event_type"] == "updated"

    assert result2["perspective_id"] == 2
    assert result2["event_count"] == 1
    assert result2["last_event_type"] == "created"

    print("PASS")