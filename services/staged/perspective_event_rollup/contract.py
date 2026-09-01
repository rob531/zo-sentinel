from fastapi import FastAPI, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import PerspectiveEvent
from sqlalchemy.orm import Session
from sqlalchemy import func
import requests

class PerspectiveEventSummary(BaseModel):
    perspective_id: int
    change_type: str
    count: int
    last_event_at: datetime

class PerspectiveEventSummaryResponse(BaseModel):
    total: int
    series: List[PerspectiveEventSummary]

app = FastAPI()

def get_perspective_events_summary(
    db: Session = Depends(get_session),
    perspective_id: Optional[int] = Query(None),
    change_type: Optional[str] = Query(None)
) -> PerspectiveEventSummaryResponse:
    query = db.query(
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
    series = [
        PerspectiveEventSummary(
            perspective_id=row.perspective_id,
            change_type=row.change_type,
            count=row.count,
            last_event_at=row.last_event_at
        ) for row in results
    ]

    return PerspectiveEventSummaryResponse(
        total=len(series),
        series=series
    )

@app.get("/api/perspective-events/summary", response_model=PerspectiveEventSummaryResponse)
async def perspective_events_summary(
    db: Session = Depends(get_session),
    perspective_id: Optional[int] = Query(None),
    change_type: Optional[str] = Query(None)
):
    return get_perspective_events_summary(db, perspective_id, change_type)

def test_perspective_events_summary():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
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
        db.add_all(test_data)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/perspective-events/summary")
    assert response.status_code == 200
    data = response.json()
    assert len(data["series"]) >= 1
    print("PASS")

if __name__ == "__main__":
    test_perspective_events_summary()