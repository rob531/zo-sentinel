from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent
from typing import Dict, Any

router = APIRouter()

def get_perspective_events_rollup(perspective_id: int, session: Session = Depends(get_session)) -> Dict[str, Dict[str, int]]:
    """Compute rollup statistics for perspective events.

    Args:
        perspective_id: The ID of the perspective to compute rollup for.
        session: SQLAlchemy session.

    Returns:
        Dictionary containing counts per change_type and new_tier.
    """
    # Query perspective events for the given perspective_id
    events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).all()

    # Initialize counters
    change_types = {}
    new_tiers = {}

    # Count occurrences of each change_type and new_tier
    for event in events:
        if event.change_type:
            change_types[event.change_type] = change_types.get(event.change_type, 0) + 1
        if event.new_tier:
            new_tiers[event.new_tier] = new_tiers.get(event.new_tier, 0) + 1

    return {
        "change_types": change_types,
        "new_tiers": new_tiers
    }

@router.get("/api/perspective/{perspective_id}/events")
async def get_events_rollup(perspective_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Endpoint to get rollup statistics for perspective events.

    Args:
        perspective_id: The ID of the perspective to compute rollup for.
        session: SQLAlchemy session.

    Returns:
        Dictionary containing rollup statistics.
    """
    return get_perspective_events_rollup(perspective_id, session)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    perspective_id = 1

    # Create test perspective events
    test_events = [
        PerspectiveEvent(
            perspective_id=perspective_id,
            change_type="added",
            new_tier="high"
        ),
        PerspectiveEvent(
            perspective_id=perspective_id,
            change_type="removed",
            new_tier="medium"
        ),
        PerspectiveEvent(
            perspective_id=perspective_id,
            change_type="added",
            new_tier="high"
        ),
        PerspectiveEvent(
            perspective_id=perspective_id,
            change_type="updated",
            new_tier="low"
        )
    ]

    session.add_all(test_events)
    session.commit()

    # Test the rollup function
    rollup = get_perspective_events_rollup(perspective_id, session)

    # Assert expected counts
    assert rollup["change_types"]["added"] == 2
    assert rollup["change_types"]["removed"] == 1
    assert rollup["change_types"]["updated"] == 1
    assert rollup["new_tiers"]["high"] == 2
    assert rollup["new_tiers"]["medium"] == 1
    assert rollup["new_tiers"]["low"] == 1

    print("PASS")