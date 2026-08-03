from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import PerspectiveEvent, Perspective
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import datetime

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

def get_perspective_events_summary(db: Session = Depends(get_session)):
    # Get all perspectives with their event counts and unseen counts
    perspectives = db.query(
        Perspective.id,
        Perspective.name,
        func.count(PerspectiveEvent.id).label('total_events'),
        func.sum(case((PerspectiveEvent.seen == False, 1), else_=0)).label('unseen_count')
    ).join(
        PerspectiveEvent, Perspective.id == PerspectiveEvent.perspective_id, isouter=True
    ).group_by(
        Perspective.id, Perspective.name
    ).all()

    # Get all transitions
    transitions = db.query(
        Perspective.id,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
        func.count(PerspectiveEvent.id).label('count')
    ).join(
        Perspective, Perspective.id == PerspectiveEvent.perspective_id
    ).group_by(
        Perspective.id, PerspectiveEvent.old_tier, PerspectiveEvent.new_tier
    ).all()

    # Build the response
    result = []
    for perspective in perspectives:
        perspective_transitions = []
        for transition in transitions:
            if transition.id == perspective.id:
                perspective_transitions.append({
                    'from_tier': transition.old_tier,
                    'to_tier': transition.new_tier,
                    'count': transition.count
                })

        result.append({
            'id': perspective.id,
            'name': perspective.name,
            'total_events': perspective.total_events,
            'unseen_count': perspective.unseen_count,
            'transitions': perspective_transitions
        })

    return ResponseModel(perspectives=result)

router.get("/")(get_perspective_events_summary)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        # Create test perspectives
        perspective1 = Perspective(name="Test Perspective 1", org_id=1)
        perspective2 = Perspective(name="Test Perspective 2", org_id=1)
        db.add_all([perspective1, perspective2])
        db.commit()

        # Create test events
        events = [
            PerspectiveEvent(
                perspective_id=perspective1.id,
                server_id=1,
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=False,
                created_at=datetime.now()
            ),
            PerspectiveEvent(
                perspective_id=perspective1.id,
                server_id=1,
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=True,
                created_at=datetime.now()
            ),
            PerspectiveEvent(
                perspective_id=perspective1.id,
                server_id=1,
                change_type="status_change",
                old_tier=None,
                new_tier=None,
                seen=False,
                created_at=datetime.now()
            ),
            PerspectiveEvent(
                perspective_id=perspective2.id,
                server_id=2,
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=True,
                created_at=datetime.now()
            ),
            PerspectiveEvent(
                perspective_id=perspective2.id,
                server_id=2,
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=True,
                created_at=datetime.now()
            ),
            PerspectiveEvent(
                perspective_id=perspective2.id,
                server_id=2,
                change_type="status_change",
                old_tier=None,
                new_tier=None,
                seen=False,
                created_at=datetime.now()
            ),
        ]
        db.add_all(events)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/perspectives/events/summary")
    assert response.status_code == 200

    # Assert response structure
    data = response.json()
    assert len(data["perspectives"]) == 2

    # Assert one known transition
    found = False
    for perspective in data["perspectives"]:
        if perspective["id"] == perspective1.id:
            for transition in perspective["transitions"]:
                if transition["from_tier"] == "low" and transition["to_tier"] == "medium":
                    found = True
                    break
            break

    assert found, "Expected transition not found"

    print("PASS")