from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session
from app.models import AskCorpusIndex
from sqlalchemy.orm import Session

router = APIRouter()

class Event(BaseModel):
    indexed_at: datetime
    snippet_preview: str
    content_hash: str

class TimelineResponse(BaseModel):
    server_id: int
    events: List[Event]

@router.get("/ask/timeline/{server_id}", response_model=TimelineResponse)
async def get_corpus_timeline(server_id: int, db: Session = Depends(get_session)):
    timeline = db.query(AskCorpusIndex).filter(AskCorpusIndex.server_id == server_id).order_by(AskCorpusIndex.indexed_at.asc()).limit(50).all()

    if not timeline:
        raise HTTPException(status_code=404, detail="No timeline found for the given server_id")

    events = [
        Event(
            indexed_at=event.indexed_at,
            snippet_preview=event.snippet_preview,
            content_hash=event.content_hash
        )
        for event in timeline
    ]

    return TimelineResponse(server_id=server_id, events=events)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import AskCorpusIndex
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    # Add test data
    test_server_id = 1
    test_data = [
        AskCorpusIndex(
            server_id=test_server_id,
            indexed_at=datetime(2023, 1, 1, 12, 0, 0),
            snippet_preview="Test snippet 1",
            content_hash="hash1"
        ),
        AskCorpusIndex(
            server_id=test_server_id,
            indexed_at=datetime(2023, 1, 2, 12, 0, 0),
            snippet_preview="Test snippet 2",
            content_hash="hash2"
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/ask/timeline/{test_server_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == test_server_id
    assert len(data["events"]) == 2
    assert "indexed_at" in data["events"][0]
    print("PASS")