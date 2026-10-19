from typing import List, Any
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot
import json

app = FastAPI()

class Membership(BaseModel):
    servers_added: int
    servers_removed: int
    tier_changes: int

class PerspectiveSnapshotResponse(BaseModel):
    id: int
    perspective_id: int
    taken_at: str
    membership: Membership

def query_snapshots(perspective_id: int, session: Session) -> List[dict]:
    results = (
        session.query(PerspectiveSnapshot, Perspective)
        .join(Perspective, PerspectiveSnapshot.perspective_id == Perspective.id)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .all()
    )
    snapshots = []
    for snap, _ in results:
        membership_data = snap.membership if isinstance(snap.membership, dict) else {}
        snapshots.append({
            "id": snap.id,
            "perspective_id": snap.perspective_id,
            "taken_at": snap.taken_at.isoformat() if snap.taken_at else None,
            "membership": Membership(
                servers_added=membership_data.get("servers_added", 0),
                servers_removed=membership_data.get("servers_removed", 0),
                tier_changes=membership_data.get("tier_changes", 0),
            ),
        })
    return snapshots

@app.get("/api/perspectives/{perspective_id}/snapshots", response_model=List[PerspectiveSnapshotResponse])
def get_perspective_snapshots(perspective_id: int, session: Session = Depends(get_session)) -> List[PerspectiveSnapshotResponse]:
    snapshots = query_snapshots(perspective_id, session)
    return [PerspectiveSnapshotResponse(**s) for s in snapshots]

def main():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    from datetime import datetime, timedelta

    p1 = Perspective(id=1, org_id=1, name="Perspective 1", description="Test", facet_filters="{}", created_by=1, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    p2 = Perspective(id=2, org_id=1, name="Perspective 2", description="Test", facet_filters="{}", created_by=1, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    session.add_all([p1, p2])
    session.commit()

    base_time = datetime.utcnow()
    for i in range(3):
        s1 = PerspectiveSnapshot(perspective_id=1, taken_at=base_time + timedelta(hours=i), membership={"servers_added": i, "servers_removed": i, "tier_changes": i})
        s2 = PerspectiveSnapshot(perspective_id=2, taken_at=base_time + timedelta(hours=i), membership={"servers_added": i + 1, "servers_removed": i + 1, "tier_changes": i + 1})
        session.add_all([s1, s2])
    session.commit()

    def override_get_session():
        try:
            yield session
        finally:
            pass

    test_app = FastAPI()
    test_app.include_router(app.router)

    client = TestClient(test_app)
    test_app.dependency_overrides[get_session] = override_get_session

    response = client.get("/api/perspectives/1/snapshots")
    data = response.json()

    assert len(data) == 3, f"Expected 3 snapshots, got {len(data)}"
    assert data[0].get("taken_at") is not None, "First item missing valid taken_at"

    print("PASS")

if __name__ == "__main__":
    main()