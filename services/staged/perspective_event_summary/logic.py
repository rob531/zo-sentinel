from typing import List, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import PerspectiveEvent, Perspective

def get_perspective_event_summary(db: Session = Depends(get_session)) -> Dict[str, List[Dict[str, Any]]]:
    """
    Aggregates perspective event counts and tier transitions per perspective.

    Returns:
        Dict[str, List[Dict[str, Any]]]: A dictionary with a single key 'perspectives' containing a list of
        perspective summaries. Each summary includes:
        - id: perspective id
        - name: perspective name
        - total_events: total number of events for the perspective
        - unseen_count: number of unseen events for the perspective
        - transitions: list of tier transition counts
    """
    # Query to get all perspectives with their event counts and unseen counts
    perspectives = db.query(
        Perspective.id,
        Perspective.name,
        PerspectiveEvent.change_type,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
        PerspectiveEvent.seen
    ).join(
        PerspectiveEvent, Perspective.id == PerspectiveEvent.perspective_id
    ).all()

    # Initialize the result structure
    result = {
        "perspectives": []
    }

    # Dictionary to hold aggregated data per perspective
    perspective_data = {}

    for perspective in perspectives:
        perspective_id = perspective.id
        name = perspective.name
        change_type = perspective.change_type
        old_tier = perspective.old_tier
        new_tier = perspective.new_tier
        seen = perspective.seen

        if perspective_id not in perspective_data:
            perspective_data[perspective_id] = {
                "id": perspective_id,
                "name": name,
                "total_events": 0,
                "unseen_count": 0,
                "transitions": {}
            }

        # Increment total events count
        perspective_data[perspective_id]["total_events"] += 1

        # Increment unseen count if event is unseen
        if not seen:
            perspective_data[perspective_id]["unseen_count"] += 1

        # Track tier transitions
        transition_key = f"{old_tier}->{new_tier}"
        if transition_key not in perspective_data[perspective_id]["transitions"]:
            perspective_data[perspective_id]["transitions"][transition_key] = {
                "from_tier": old_tier,
                "to_tier": new_tier,
                "count": 0
            }
        perspective_data[perspective_id]["transitions"][transition_key]["count"] += 1

    # Convert the transitions dictionary to a list
    for perspective_id in perspective_data:
        transitions_list = list(perspective_data[perspective_id]["transitions"].values())
        perspective_data[perspective_id]["transitions"] = transitions_list
        result["perspectives"].append(perspective_data[perspective_id])

    return result

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create a test client
    client = TestClient(app)

    # Seed test data
    session = SessionLocal()
    try:
        # Create test perspectives
        perspective1 = Perspective(id=1, org_id=1, name="Test Perspective 1")
        perspective2 = Perspective(id=2, org_id=1, name="Test Perspective 2")
        session.add_all([perspective1, perspective2])

        # Create test events
        events = [
            PerspectiveEvent(
                id=1,
                perspective_id=1,
                server_id=1,
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=False,
                created_at="2023-01-01T00:00:00"
            ),
            PerspectiveEvent(
                id=2,
                perspective_id=1,
                server_id=2,
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=True,
                created_at="2023-01-02T00:00:00"
            ),
            PerspectiveEvent(
                id=3,
                perspective_id=1,
                server_id=3,
                change_type="tier_change",
                old_tier="high",
                new_tier="low",
                seen=False,
                created_at="2023-01-03T00:00:00"
            ),
            PerspectiveEvent(
                id=4,
                perspective_id=2,
                server_id=4,
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=True,
                created_at="2023-01-04T00:00:00"
            ),
            PerspectiveEvent(
                id=5,
                perspective_id=2,
                server_id=5,
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=True,
                created_at="2023-01-05T00:00:00"
            ),
            PerspectiveEvent(
                id=6,
                perspective_id=2,
                server_id=6,
                change_type="tier_change",
                old_tier="high",
                new_tier="low",
                seen=False,
                created_at="2023-01-06T00:00:00"
            )
        ]
        session.add_all(events)
        session.commit()
    finally:
        session.close()

    # Test the function
    summary = get_perspective_event_summary()
    assert len(summary["perspectives"]) == 2
    assert summary["perspectives"][0]["id"] == 1
    assert summary["perspectives"][0]["name"] == "Test Perspective 1"
    assert summary["perspectives"][0]["total_events"] == 3
    assert summary["perspectives"][0]["unseen_count"] == 2
    assert len(summary["perspectives"][0]["transitions"]) == 3
    assert summary["perspectives"][0]["transitions"][0]["from_tier"] == "low"
    assert summary["perspectives"][0]["transitions"][0]["to_tier"] == "medium"
    assert summary["perspectives"][0]["transitions"][0]["count"] == 1

    print("PASS")