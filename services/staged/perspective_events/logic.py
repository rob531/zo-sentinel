from typing import List, Optional
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import PerspectiveEvent, McpServerRegistry

class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: str

class PerspectiveEventsResponse(BaseModel):
    events: List[PerspectiveEventResponse]

def get_perspective_events(
    perspective_id: int,
    session: Session = Depends(get_session),
    limit: int = 100,
    offset: int = 0
) -> PerspectiveEventsResponse:
    query = select(
        PerspectiveEvent.id,
        PerspectiveEvent.server_id,
        McpServerRegistry.name.label('server_name'),
        PerspectiveEvent.change_type,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
        PerspectiveEvent.seen,
        PerspectiveEvent.created_at
    ).join(
        McpServerRegistry,
        PerspectiveEvent.server_id == McpServerRegistry.id
    ).where(
        PerspectiveEvent.perspective_id == perspective_id
    ).order_by(
        PerspectiveEvent.created_at.desc()
    ).limit(limit).offset(offset)

    result = session.execute(query)
    events = [
        PerspectiveEventResponse(
            id=row.id,
            server_id=row.server_id,
            server_name=row.server_name,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            seen=row.seen,
            created_at=str(row.created_at)
        ) for row in result
    ]

    return PerspectiveEventsResponse(events=events)

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import Perspective, PerspectiveEvent, McpServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_session = SessionLocal()

    # Create test data
    test_server1 = McpServerRegistry(name="Server 1", ip_address="192.168.1.1")
    test_server2 = McpServerRegistry(name="Server 2", ip_address="192.168.1.2")
    test_perspective = Perspective(name="Test Perspective")

    test_session.add_all([test_server1, test_server2, test_perspective])
    test_session.commit()

    test_event1 = PerspectiveEvent(
        perspective_id=test_perspective.id,
        server_id=test_server1.id,
        change_type="tier_change",
        old_tier="low",
        new_tier="medium",
        seen=True
    )
    test_event2 = PerspectiveEvent(
        perspective_id=test_perspective.id,
        server_id=test_server2.id,
        change_type="tier_change",
        old_tier="medium",
        new_tier="high",
        seen=False
    )

    test_session.add_all([test_event1, test_event2])
    test_session.commit()

    # Test the function
    result = get_perspective_events(test_perspective.id, test_session)

    if len(result.events) == 2:
        print("PASS")
    else:
        print("FAIL")

    # Clean up
    test_session.query(PerspectiveEvent).delete()
    test_session.query(McpServerRegistry).delete()
    test_session.query(Perspective).delete()
    test_session.commit()
    test_session.close()