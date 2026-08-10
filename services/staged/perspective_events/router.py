from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import PerspectiveEvent, McpServerRegistry

router = APIRouter(prefix="/api", tags=["PerspectiveEvent"])

class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime

    class Config:
        from_attributes = True

class EventsListResponse(BaseModel):
    events: List[PerspectiveEventResponse]

def get_perspective_events(db: Session, perspective_id: int, skip: int = 0, limit: int = 100) -> List[dict]:
    """Fetch paginated perspective events joined with server registry."""
    stmt = (
        select(
            PerspectiveEvent,
            McpServerRegistry.c.server_name
        )
        .join(
            McpServerRegistry,
            PerspectiveEvent.c.server_id == McpServerRegistry.c.id
        )
        .where(PerspectiveEvent.c.perspective_id == perspective_id)
        .offset(skip)
        .limit(limit)
    )
    results = db.execute(stmt).fetchall()
    return [
        {
            "id": row[0].id,
            "server_id": row[0].server_id,
            "server_name": row[1],
            "change_type": row[0].change_type,
            "old_tier": row[0].old_tier,
            "new_tier": row[0].new_tier,
            "seen": row[0].seen,
            "created_at": row[0].created_at,
        }
        for row in results
    ]

@router.get("/perspective/{perspective_id}/events", response_model=EventsListResponse)
def get_events(
    perspective_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_session)
) -> EventsListResponse:
    """GET /api/perspective/{perspective_id}/events - returns paginated events with server details."""
    events = get_perspective_events(db, perspective_id, skip, limit)
    return EventsListResponse(events=events)

if __name__ == "__main__":
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from app.main import app

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE McpServerRegistry (
            id INTEGER PRIMARY KEY,
            server_name TEXT NOT NULL,
            server_type TEXT,
            capability TEXT,
            risk_score REAL,
            risk_tier TEXT,
            tags TEXT,
            tier_history TEXT,
            verified BOOLEAN,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        CREATE TABLE PerspectiveEvent (
            id INTEGER PRIMARY KEY,
            perspective_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            old_tier TEXT,
            new_tier TEXT,
            seen BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES McpServerRegistry(id)
        );
        INSERT INTO McpServerRegistry (id, server_name) VALUES (1, 'test-server');
        INSERT INTO PerspectiveEvent (perspective_id, server_id, change_type, old_tier, new_tier, seen)
        VALUES (1, 1, 'tier_change', 'low', 'high', 0);
        INSERT INTO PerspectiveEvent (perspective_id, server_id, change_type, old_tier, new_tier, seen)
        VALUES (1, 1, 'tier_change', 'medium', 'critical', 1);
    """)
    conn.row_factory = sqlite3.Row

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        native_datetime=True
    )

    for line in conn.iterdump():
        if line.startswith("CREATE") or line.startswith("INSERT"):
            try:
                engine.execute(line)
            except:
                pass

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/perspective/1/events")
    data = response.json()
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert "events" in data, "Response missing 'events' key"
    assert len(data["events"]) == 2, f"Expected 2 events, got {len(data['events'])}"
    
    for event in data["events"]:
        assert "id" in event
        assert "server_id" in event
        assert "server_name" in event
        assert "change_type" in event
        assert "seen" in event
        assert "created_at" in event
    
    app.dependency_overrides.clear()
    print("PASS")