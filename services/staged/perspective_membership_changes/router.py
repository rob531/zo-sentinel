from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import PerspectiveEvent, Base
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

router = APIRouter()


class MembershipChange(BaseModel):
    server_id: int
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


class MembershipChangesResponse(BaseModel):
    changes: List[MembershipChange]


@router.get("/perspectives/{perspective_id}/membership/changes", response_model=MembershipChangesResponse)
def get_perspective_membership_changes(
    perspective_id: int,
    skip: int = 0,
    limit: int = 100,
    session: Session = Depends(get_session)
):
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(desc(PerspectiveEvent.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )
    
    return MembershipChangesResponse(
        changes=[
            MembershipChange(
                server_id=e.server_id,
                change_type=e.change_type,
                old_tier=e.old_tier,
                new_tier=e.new_tier,
                seen=e.seen,
                created_at=e.created_at
            )
            for e in events
        ]
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    def seed_data():
        db = TestingSessionLocal()
        events = [
            PerspectiveEvent(perspective_id=1, server_id=100, change_type="added", old_tier=None, new_tier="tier1", seen=False, created_at=datetime(2024, 1, 15, 10, 30, 0)),
            PerspectiveEvent(perspective_id=1, server_id=101, change_type="updated", old_tier="tier1", new_tier="tier2", seen=True, created_at=datetime(2024, 1, 16, 11, 0, 0)),
            PerspectiveEvent(perspective_id=2, server_id=100, change_type="removed", old_tier="tier1", new_tier=None, seen=True, created_at=datetime(2024, 1, 17, 14, 0, 0)),
        ]
        db.add_all(events)
        db.commit()
        db.close()
    
    seed_data()
    
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/perspectives/1/membership/changes")
    assert response.status_code == 200
    data = response.json()
    assert len(data["changes"]) == 2
    assert data["changes"][0]["server_id"] == 101
    assert data["changes"][0]["change_type"] == "updated"
    assert data["changes"][1]["server_id"] == 100
    assert data["changes"][1]["change_type"] == "added"
    print("PASS")