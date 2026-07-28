from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import PerspectiveEvent, Perspective

router = APIRouter(prefix="/api/perspectives/events/summary")

class Transition(BaseModel):
    from_tier: Optional[str]
    to_tier: Optional[str]
    count: int

class PerspectiveSummary(BaseModel):
    id: int
    name: str
    total_events: int
    unseen_count: int
    transitions: List[Transition]

class ResponseModel(BaseModel):
    perspectives: List[PerspectiveSummary]

@router.get("/", response_model=ResponseModel)
async def get_perspective_event_summary(session: Session = Depends(get_session)):
    # Query perspectives with their event counts and transitions
    query = session.query(
        Perspective.id,
        Perspective.name,
        PerspectiveEvent.change_type,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
        PerspectiveEvent.seen
    ).join(
        PerspectiveEvent, Perspective.id == PerspectiveEvent.perspective_id
    ).all()

    # Process the query results
    perspective_dict = {}
    for row in query:
        perspective_id = row.id
        if perspective_id not in perspective_dict:
            perspective_dict[perspective_id] = {
                "id": perspective_id,
                "name": row.name,
                "total_events": 0,
                "unseen_count": 0,
                "transitions": {}
            }

        perspective = perspective_dict[perspective_id]
        perspective["total_events"] += 1
        if not row.seen:
            perspective["unseen_count"] += 1

        # Handle transitions
        change_type = row.change_type
        old_tier = row.old_tier
        new_tier = row.new_tier
        transition_key = (old_tier, new_tier)

        if transition_key not in perspective["transitions"]:
            perspective["transitions"][transition_key] = {
                "from_tier": old_tier,
                "to_tier": new_tier,
                "count": 0
            }
        perspective["transitions"][transition_key]["count"] += 1

    # Convert to the response format
    perspectives = []
    for perspective_id, data in perspective_dict.items():
        transitions = list(data["transitions"].values())
        perspectives.append({
            "id": data["id"],
            "name": data["name"],
            "total_events": data["total_events"],
            "unseen_count": data["unseen_count"],
            "transitions": transitions
        })

    return {"perspectives": perspectives}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    session = TestSession()
    try:
        # Create test perspectives
        perspective1 = Perspective(name="Test Perspective 1", org_id=1)
        perspective2 = Perspective(name="Test Perspective 2", org_id=1)
        session.add_all([perspective1, perspective2])
        session.commit()

        # Create test events
        events = [
            PerspectiveEvent(
                perspective_id=perspective1.id,
                server_id=1,
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=True,
                created_at="2023-01-01"
            ),
            PerspectiveEvent(
                perspective_id=perspective1.id,
                server_id=2,
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=False,
                created_at="2023-01-02"
            ),
            PerspectiveEvent(
                perspective_id=perspective1.id,
                server_id=3,
                change_type="status_change",
                old_tier=None,
                new_tier=None,
                seen=True,
                created_at="2023-01-03"
            ),
            PerspectiveEvent(
                perspective_id=perspective2.id,
                server_id=4,
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=True,
                created_at="2023-01-04"
            ),
            PerspectiveEvent(
                perspective_id=perspective2.id,
                server_id=5,
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=True,
                created_at="2023-01-05"
            ),
            PerspectiveEvent(
                perspective_id=perspective2.id,
                server_id=6,
                change_type="status_change",
                old_tier=None,
                new_tier=None,
                seen=False,
                created_at="2023-01-06"
            )
        ]
        session.add_all(events)
        session.commit()
    finally:
        session.close()

    # Run test
    client = TestClient(app)
    response = client.get("/api/perspectives/events/summary")
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert len(data["perspectives"]) == 2
    perspective1 = next(p for p in data["perspectives"] if p["id"] == perspective1.id)
    assert perspective1["total_events"] == 3
    assert perspective1["unseen_count"] == 1
    assert any(t["from_tier"] == "low" and t["to_tier"] == "medium" for t in perspective1["transitions"])

    print("PASS")