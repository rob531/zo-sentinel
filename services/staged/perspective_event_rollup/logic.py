from typing import List, Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import PerspectiveEvent
from pydantic import BaseModel
from datetime import datetime

class PerspectiveEventSummary(BaseModel):
    perspective_id: int
    change_type: str
    count: int
    last_event_at: datetime

def get_perspective_event_summary(
    session: Session = Depends(get_session),
    perspective_id: Optional[int] = None,
    change_type: Optional[str] = None
) -> List[PerspectiveEventSummary]:
    query = session.query(
        PerspectiveEvent.perspective_id,
        PerspectiveEvent.change_type,
        func.count(PerspectiveEvent.id).label('count'),
        func.max(PerspectiveEvent.created_at).label('last_event_at')
    ).group_by(
        PerspectiveEvent.perspective_id,
        PerspectiveEvent.change_type
    )

    if perspective_id is not None:
        query = query.filter(PerspectiveEvent.perspective_id == perspective_id)
    if change_type is not None:
        query = query.filter(PerspectiveEvent.change_type == change_type)

    results = query.all()
    return [
        PerspectiveEventSummary(
            perspective_id=row.perspective_id,
            change_type=row.change_type,
            count=row.count,
            last_event_at=row.last_event_at
        )
        for row in results
    ]

if __name__ == "__main__":
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    from app.models import PerspectiveEvent
    test_data = [
        PerspectiveEvent(
            perspective_id=1,
            change_type="created",
            created_at=datetime.now()
        ),
        PerspectiveEvent(
            perspective_id=1,
            change_type="updated",
            created_at=datetime.now()
        ),
        PerspectiveEvent(
            perspective_id=2,
            change_type="created",
            created_at=datetime.now()
        ),
        PerspectiveEvent(
            perspective_id=2,
            change_type="updated",
            created_at=datetime.now()
        ),
        PerspectiveEvent(
            perspective_id=3,
            change_type="created",
            created_at=datetime.now()
        ),
        PerspectiveEvent(
            perspective_id=3,
            change_type="updated",
            created_at=datetime.now()
        ),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the function
    try:
        results = get_perspective_event_summary()
        assert len(results) >= 1
        print("PASS")
    except AssertionError:
        print("FAIL")
    finally:
        test_session.close()