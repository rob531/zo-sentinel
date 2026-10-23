from typing import List, Dict, Any
from datetime import datetime
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db import get_session
from app.models import PerspectiveSnapshot, PerspectiveEvent

class Event(BaseModel):
    server_id: str
    change_type: str
    old_tier: str
    new_tier: str
    seen_flag: bool

class Snapshot(BaseModel):
    id: int
    taken_at: datetime
    membership: Dict[str, Any]
    count: int
    events: List[Event]

def get_snapshot_history(perspective_id: int, session: Session = Depends(get_session)) -> List[Snapshot]:
    # Get all snapshots for the perspective, ordered by taken_at
    snapshots = session.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.perspective_id == perspective_id
    ).order_by(PerspectiveSnapshot.taken_at).all()

    if not snapshots:
        raise HTTPException(status_code=404, detail="No snapshots found for this perspective")

    result = []

    for snapshot in snapshots:
        # Get all events for this perspective that occurred after this snapshot
        events = session.query(PerspectiveEvent).filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.created_at > snapshot.taken_at
        ).all()

        # Convert events to the required format
        event_list = [
            Event(
                server_id=event.server_id,
                change_type=event.change_type,
                old_tier=event.old_tier,
                new_tier=event.new_tier,
                seen_flag=event.seen
            ) for event in events
        ]

        # Create the snapshot object
        snapshot_obj = Snapshot(
            id=snapshot.id,
            taken_at=snapshot.taken_at,
            membership=snapshot.membership,
            count=len(snapshot.membership.get('servers', [])),
            events=event_list
        )

        result.append(snapshot_obj)

    return result

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create a test FastAPI app
    test_app = FastAPI()

    # Override the get_session dependency for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Create test data
    def create_test_data(session: Session):
        # Create a perspective
        perspective_id = 1

        # Create snapshots
        snapshot1 = PerspectiveSnapshot(
            perspective_id=perspective_id,
            taken_at=datetime(2023, 1, 1),
            membership={"servers": ["server1", "server2"]}
        )
        snapshot2 = PerspectiveSnapshot(
            perspective_id=perspective_id,
            taken_at=datetime(2023, 1, 2),
            membership={"servers": ["server1", "server2", "server3"]}
        )
        session.add_all([snapshot1, snapshot2])

        # Create events
        event1 = PerspectiveEvent(
            perspective_id=perspective_id,
            server_id="server1",
            change_type="tier_change",
            old_tier="tier1",
            new_tier="tier2",
            seen=True,
            created_at=datetime(2023, 1, 1, 12, 0)
        )
        event2 = PerspectiveEvent(
            perspective_id=perspective_id,
            server_id="server2",
            change_type="tier_change",
            old_tier="tier1",
            new_tier="tier3",
            seen=False,
            created_at=datetime(2023, 1, 1, 13, 0)
        )
        event3 = PerspectiveEvent(
            perspective_id=perspective_id,
            server_id="server3",
            change_type="tier_change",
            old_tier="tier2",
            new_tier="tier1",
            seen=True,
            created_at=datetime(2023, 1, 2, 14, 0)
        )
        session.add_all([event1, event2, event3])
        session.commit()

    # Run the test
    with SessionLocal() as session:
        create_test_data(session)

        # Get the snapshot history
        history = get_snapshot_history(1, session)

        # Verify the results
        assert len(history) == 2
        event_count = sum(len(snapshot.events) for snapshot in history)
        assert event_count >= 3

        print("PASS")