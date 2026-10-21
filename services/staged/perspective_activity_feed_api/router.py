from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot

router = APIRouter()


class EventShape(BaseModel):
    id: int
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ActivityFeedResponse(BaseModel):
    perspective_id: int
    events: List[EventShape]
    snapshot_count: int
    total_events: int


@router.get("/api/perspectives/{perspective_id}/activity", response_model=ActivityFeedResponse)
def get_perspective_activity(
    perspective_id: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    total_events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).count()

    events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).order_by(
        PerspectiveEvent.created_at.desc()
    ).offset(offset).limit(limit).all()

    snapshot_count = session.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.perspective_id == perspective_id
    ).count()

    return ActivityFeedResponse(
        perspective_id=perspective_id,
        events=[EventShape.model_validate(e) for e in events],
        snapshot_count=snapshot_count,
        total_events=total_events,
    )


@router.get("/api/perspectives/activity/global", response_model=ActivityFeedResponse)
def get_global_activity(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    total_events = session.query(PerspectiveEvent).count()

    events = session.query(PerspectiveEvent).order_by(
        PerspectiveEvent.created_at.desc()
    ).offset(offset).limit(limit).all()

    snapshot_count = session.query(PerspectiveSnapshot).count()

    return ActivityFeedResponse(
        perspective_id=0,
        events=[EventShape.model_validate(e) for e in events],
        snapshot_count=snapshot_count,
        total_events=total_events,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    pe1 = PerspectiveEvent(
        id=1,
        perspective_id=10,
        change_type="tier_change",
        old_tier="low",
        new_tier="high",
        seen=False,
        created_at=datetime(2024, 1, 15, 10, 30, 0),
        server_id=100,
    )
    pe2 = PerspectiveEvent(
        id=2,
        perspective_id=10,
        change_type="tier_change",
        old_tier="high",
        new_tier="medium",
        seen=True,
        created_at=datetime(2024, 1, 16, 11, 0, 0),
        server_id=101,
    )
    ps1 = PerspectiveSnapshot(
        id=1,
        perspective_id=10,
        membership=[],
        taken_at=datetime(2024, 1, 15, 12, 0, 0),
    )
    test_session.add_all([pe1, pe2, ps1])
    test_session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    resp = client.get("/api/perspectives/10/activity")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert len(data["events"]) == 2, f"Expected 2 events, got {len(data['events'])}"
    assert data["perspective_id"] == 10
    assert data["snapshot_count"] == 1
    assert data["total_events"] == 2
    ev = data["events"][0]
    assert "id" in ev
    assert "change_type" in ev
    assert "old_tier" in ev
    assert "new_tier" in ev
    assert "seen" in ev
    assert "created_at" in ev

    resp_global = client.get("/api/perspectives/activity/global")
    assert resp_global.status_code == 200
    global_data = resp_global.json()
    assert len(global_data["events"]) == 2

    print("PASS")