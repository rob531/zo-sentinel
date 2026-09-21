"""
Perspective Snapshot Rollup Router
"""
from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base

router = APIRouter(prefix="/api", tags=["perspective-snapshot-rollup"])


class SnapshotItem(BaseModel):
    id: int
    perspective_id: int
    taken_at: str
    membership_count: int


class SnapshotsResponse(BaseModel):
    snapshots: List[SnapshotItem]


def get_perspective_snapshots_logic(
    session: Session,
    perspective_id: int
) -> dict:
    """Query perspective_snapshots joined to perspectives, return snapshots with membership_count."""
    query = text("""
        SELECT ps.id, ps.perspective_id, ps.taken_at, ps.membership
        FROM perspective_snapshots ps
        INNER JOIN perspectives p ON p.id = ps.perspective_id
        WHERE ps.perspective_id = :perspective_id
        ORDER BY ps.taken_at
    """)
    
    result = session.execute(query, {"perspective_id": perspective_id})
    rows = result.fetchall()
    
    snapshots = []
    for row in rows:
        membership = row.membership or []
        if isinstance(membership, str):
            import json
            membership = json.loads(membership)
        membership_count = len(membership)
        
        snapshots.append({
            "id": row.id,
            "perspective_id": row.perspective_id,
            "taken_at": row.taken_at.isoformat() if hasattr(row.taken_at, 'isoformat') else str(row.taken_at),
            "membership_count": membership_count
        })
    
    return {"snapshots": snapshots}


@router.get(
    "/perspectives/{perspective_id}/snapshots",
    response_model=SnapshotsResponse
)
def get_perspectives_snapshots(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> dict:
    """Get snapshots for a perspective with membership counts."""
    return get_perspective_snapshots_logic(session, perspective_id)


if __name__ == "__main__":
    from datetime import datetime, timezone
    import json
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session as SA_Session
    from sqlalchemy.pool import StaticPool
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    
    def override_get_session() -> SA_Session:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    with TestingSessionLocal() as session:
        session.execute(text("""
            INSERT INTO perspectives (id, name, description, org_id, created_by, created_at, updated_at, facet_filters)
            VALUES 
                (1, 'Perspective 1', 'First test perspective', 1, 1, :now, :now, '{}'),
                (2, 'Perspective 2', 'Second test perspective', 1, 1, :now, :now, '{}')
        """), {"now": datetime.now(timezone.utc)})
        
        session.execute(text("""
            INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership)
            VALUES 
                (1, 1, :now, '["user1", "user2", "user3"]'),
                (2, 1, :now, '["user4", "user5"]'),
                (3, 1, :now, '["user6"]'),
                (4, 2, :now, '["user7", "user8", "user9", "user10"]'),
                (5, 2, :now, '["user11"]'),
                (6, 2, :now, '["user12", "user13"]')
        """), {"now": datetime.now(timezone.utc)})
        
        session.commit()
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/api/perspectives/1/snapshots")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["snapshots"]) == 3, f"Expected 3 snapshots for perspective 1, got {len(data['snapshots'])}"
    
    response2 = client.get("/api/perspectives/2/snapshots")
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2["snapshots"]) == 3, f"Expected 3 snapshots for perspective 2, got {len(data2['snapshots'])}"
    
    total_snapshots = len(data["snapshots"]) + len(data2["snapshots"])
    assert total_snapshots == 6, f"Expected total 6 snapshots, got {total_snapshots}"
    
    print("PASS")