from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import PerspectiveSnapshot, PerspectiveEvent
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any
import json

router = APIRouter(prefix="/api", tags=["perspective"])

class TransitionRecord(BaseModel):
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    at: datetime

class PerspectiveDiffResponse(BaseModel):
    perspective_id: str
    snapshot_a: str
    snapshot_b: str
    servers_added: list[str]
    servers_removed: list[str]
    recent_transitions: list[TransitionRecord]

def _parse_membership(membership: Any) -> list:
    if isinstance(membership, list):
        return membership
    if isinstance(membership, str):
        try:
            return json.loads(membership)
        except:
            return [membership]
    return [membership]

@router.get("/perspectives/{perspective_id}/diff", response_model=PerspectiveDiffResponse)
def get_perspective_diff(
    perspective_id: str,
    snapshot_a: str = Query(...),
    snapshot_b: str = Query(...),
    session: Session = Depends(get_session)
):
    snap_a = session.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.id == snapshot_a,
        PerspectiveSnapshot.perspective_id == perspective_id
    ).first()
    
    snap_b = session.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.id == snapshot_b,
        PerspectiveSnapshot.perspective_id == perspective_id
    ).first()
    
    if not snap_a or not snap_b:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    membership_a = set(_parse_membership(snap_a.membership))
    membership_b = set(_parse_membership(snap_b.membership))
    
    servers_added = list(membership_b - membership_a)
    servers_removed = list(membership_a - membership_b)
    
    events = session.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).order_by(PerspectiveEvent.created_at.desc()).limit(10).all()
    
    transitions = [
        TransitionRecord(
            server_id=e.server_id,
            change_type=e.change_type,
            old_tier=e.old_tier,
            new_tier=e.new_tier,
            at=e.created_at
        )
        for e in events
    ]
    
    return PerspectiveDiffResponse(
        perspective_id=perspective_id,
        snapshot_a=snapshot_a,
        snapshot_b=snapshot_b,
        servers_added=servers_added,
        servers_removed=servers_removed,
        recent_transitions=transitions
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine, Table, Column, String, DateTime, JSON, MetaData, Integer, Text
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy.sql import text
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    metadata = MetaData()
    
    Table(
        "perspective_snapshots",
        metadata,
        Column("id", String, primary_key=True),
        Column("perspective_id", String),
        Column("taken_at", DateTime),
        Column("membership", Text)
    )
    
    Table(
        "perspective_events",
        metadata,
        Column("id", String, primary_key=True),
        Column("perspective_id", String),
        Column("server_id", String),
        Column("change_type", String),
        Column("old_tier", String),
        Column("new_tier", String),
        Column("seen", Integer),
        Column("created_at", DateTime)
    )
    
    metadata.create_all(engine)
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership)
            VALUES 
                ('snap-a', 'persp-1', '2024-01-01T00:00:00', '["srv-x", "srv-y", "srv-z"]'),
                ('snap-b', 'persp-1', '2024-01-02T00:00:00', '["srv-y", "srv-z", "srv-w"]')
        """))
        
        conn.execute(text("""
            INSERT INTO perspective_events (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
            VALUES 
                ('evt-1', 'persp-1', 'srv-w', 'tier_change', 'basic', 'premium', 1, '2024-01-02T00:00:00')
        """))
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(that_app)
    resp = client.get("/api/perspectives/persp-1/diff", params={"snapshot_a": "snap-a", "snapshot_b": "snap-b"})
    
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    
    assert "srv-w" in data["servers_added"], f"srv-w should be in servers_added: {data['servers_added']}"
    assert "srv-x" in data["servers_removed"], f"srv-x should be in servers_removed: {data['servers_removed']}"
    assert len(data["servers_added"]) > 0, "servers_added should be non-empty"
    assert len(data["servers_removed"]) > 0, "servers_removed should be non-empty"
    assert len(data["recent_transitions"]) > 0, "recent_transitions should be non-empty"
    
    print("PASS")