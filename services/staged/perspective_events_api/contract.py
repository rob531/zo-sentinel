from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Optional, List
from datetime import datetime

from app.db import get_session
from app.models import PerspectiveEvent, Perspective

router = APIRouter(prefix="/api", tags=["perspective_events"])


class PerspectiveEventResponse(BaseModel):
    id: int
    perspective_id: int
    server_id: int
    change_type: str
    old_tier: Optional[int]
    new_tier: Optional[int]
    seen: bool
    created_at: datetime

    class Config:
        from_attributes = True


def get_perspective_events(
    perspective_id: int,
    seen: Optional[bool],
    db: Session
) -> List[PerspectiveEventResponse]:
    query = db.query(PerspectiveEvent).filter(PerspectiveEvent.perspective_id == perspective_id)
    if seen is not None:
        query = query.filter(PerspectiveEvent.seen == seen)
    query = query.order_by(PerspectiveEvent.created_at.desc())
    rows = query.all()
    return [PerspectiveEventResponse.model_validate(row) for row in rows]


@router.get("/perspectives/{perspective_id}/events", response_model=List[PerspectiveEventResponse])
def list_perspective_events(
    perspective_id: int,
    seen: Optional[bool] = None,
    db: Session = Depends(get_session)
) -> List[PerspectiveEventResponse]:
    return get_perspective_events(perspective_id, seen, db)


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE perspectives (id INTEGER PRIMARY KEY, name TEXT, org_id INTEGER, created_by INTEGER, description TEXT, facet_filters TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE perspective_events (id INTEGER PRIMARY KEY, perspective_id INTEGER, server_id INTEGER, change_type TEXT, old_tier INTEGER, new_tier INTEGER, seen BOOLEAN, created_at TIMESTAMP)"))

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_db

    db = SessionLocal()
    p1 = Perspective(id=1, name="Perspective 1", org_id=1, created_by=1)
    p2 = Perspective(id=2, name="Perspective 2", org_id=1, created_by=1)
    db.add(p1)
    db.add(p2)
    now = datetime.utcnow()

    events = [
        PerspectiveEvent(id=1, perspective_id=1, server_id=10, change_type="tier_change", old_tier=1, new_tier=2, seen=True, created_at=now),
        PerspectiveEvent(id=2, perspective_id=1, server_id=11, change_type="tier_change", old_tier=2, new_tier=3, seen=False, created_at=now),
        PerspectiveEvent(id=3, perspective_id=1, server_id=12, change_type="tier_change", old_tier=1, new_tier=2, seen=True, created_at=now),
        PerspectiveEvent(id=4, perspective_id=1, server_id=13, change_type="tier_change", old_tier=3, new_tier=1, seen=False, created_at=now),
        PerspectiveEvent(id=5, perspective_id=2, server_id=20, change_type="tier_change", old_tier=1, new_tier=2, seen=True, created_at=now),
        PerspectiveEvent(id=6, perspective_id=2, server_id=21, change_type="tier_change", old_tier=2, new_tier=3, seen=False, created_at=now),
        PerspectiveEvent(id=7, perspective_id=2, server_id=22, change_type="tier_change", old_tier=1, new_tier=2, seen=True, created_at=now),
        PerspectiveEvent(id=8, perspective_id=2, server_id=23, change_type="tier_change", old_tier=3, new_tier=1, seen=False, created_at=now),
    ]
    for e in events:
        db.add(e)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/api/perspectives/1/events", params={"seen": True})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for item in data:
        assert item["perspective_id"] == 1
        assert item["seen"] is True
    print("PASS")