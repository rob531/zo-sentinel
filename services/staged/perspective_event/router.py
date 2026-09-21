from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent
from .logic import get_perspective_events

router = APIRouter(prefix="/api")

@router.get("/perspective/{perspective_id}/events")
async def get_events(
    perspective_id: int,
    session: Session = Depends(get_session)
):
    return await get_perspective_events(perspective_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    # Create test data
    session = TestingSessionLocal()
    perspective_id = 1
    session.add_all([
        McpServerRegistry(server_id=1, server_uuid="uuid1", perspective_id=perspective_id),
        McpServerRegistry(server_id=2, server_uuid="uuid2", perspective_id=perspective_id),
        PerspectiveEvent(perspective_id=perspective_id, server_id=1, change_type="tier_change", old_tier=1, new_tier=2, seen=True),
        PerspectiveEvent(perspective_id=perspective_id, server_id=2, change_type="tier_change", old_tier=2, new_tier=3, seen=False),
    ])
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/perspective/{perspective_id}/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) == 2
    assert data["perspective_id"] == perspective_id
    assert any(event["server_id"] == 1 for event in data["events"])

    print("PASS")