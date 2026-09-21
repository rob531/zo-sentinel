from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi import Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot

class ServerChangeType(BaseModel):
    server_id: int
    change_type: str

class PerspectiveEventsSummary(BaseModel):
    event_count: int
    last_event_date: Optional[datetime]
    top_servers: List[ServerChangeType]

def get_perspective_events_summary(perspective_id: int, session: Session = Depends(get_session)) -> PerspectiveEventsSummary:
    # Get event count and last event date
    event_stats = session.query(
        func.count(PerspectiveEvent.id).label('event_count'),
        func.max(PerspectiveEvent.created_at).label('last_event_date')
    ).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).first()

    event_count = event_stats.event_count if event_stats else 0
    last_event_date = event_stats.last_event_date if event_stats and event_stats.last_event_date else None

    # Get top servers by change type frequency
    top_servers = session.query(
        PerspectiveEvent.server_id,
        PerspectiveEvent.change_type,
        func.count(PerspectiveEvent.id).label('change_count')
    ).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).group_by(
        PerspectiveEvent.server_id,
        PerspectiveEvent.change_type
    ).order_by(
        func.count(PerspectiveEvent.id).desc()
    ).limit(5).all()

    top_servers_list = [
        ServerChangeType(server_id=server.server_id, change_type=server.change_type)
        for server in top_servers
    ]

    return PerspectiveEventsSummary(
        event_count=event_count,
        last_event_date=last_event_date,
        top_servers=top_servers_list
    )

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

    # Override dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Add test route
    @app.get("/api/perspectives/{perspective_id}/events/summary")
    async def test_get_perspective_events_summary(perspective_id: int, session: Session = Depends(get_session)):
        return get_perspective_events_summary(perspective_id, session)

    # Seed test data
    session = SessionLocal()
    try:
        # Create test perspective
        perspective_id = 1

        # Add test events
        test_events = [
            PerspectiveEvent(
                perspective_id=perspective_id,
                server_id=1,
                change_type="added",
                created_at=datetime(2023, 1, 1)
            ),
            PerspectiveEvent(
                perspective_id=perspective_id,
                server_id=2,
                change_type="removed",
                created_at=datetime(2023, 1, 2)
            ),
            PerspectiveEvent(
                perspective_id=perspective_id,
                server_id=1,
                change_type="added",
                created_at=datetime(2023, 1, 3)
            ),
            PerspectiveEvent(
                perspective_id=perspective_id,
                server_id=3,
                change_type="modified",
                created_at=datetime(2023, 1, 4)
            ),
            PerspectiveEvent(
                perspective_id=perspective_id,
                server_id=1,
                change_type="added",
                created_at=datetime(2023, 1, 5)
            ),
        ]
        session.add_all(test_events)
        session.commit()

        # Test client
        client = TestClient(app)

        # Test endpoint
        response = client.get(f"/api/perspectives/{perspective_id}/events/summary")
        assert response.status_code == 200
        data = response.json()

        assert data["event_count"] == 5
        assert data["last_event_date"] == "2023-01-05T00:00:00"
        assert len(data["top_servers"]) == 3
        assert data["top_servers"][0]["server_id"] == 1
        assert data["top_servers"][0]["change_type"] == "added"
        assert data["top_servers"][1]["server_id"] == 2
        assert data["top_servers"][1]["change_type"] == "removed"
        assert data["top_servers"][2]["server_id"] == 3
        assert data["top_servers"][2]["change_type"] == "modified"

        print("PASS")
    finally:
        session.close()