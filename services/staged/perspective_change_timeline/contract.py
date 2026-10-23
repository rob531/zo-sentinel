from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api/perspectives", tags=["perspective_change_timeline"])


class TimelineEvent(BaseModel):
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    created_at: datetime


class TimelineResponse(BaseModel):
    perspective_id: int
    days: int
    events: List[TimelineEvent]


@router.get("/{perspective_id}/timeline", response_model=TimelineResponse)
def get_perspective_timeline(
    perspective_id: int,
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TimelineResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    query = text("""
        SELECT 
            pe.server_id,
            msr.name as server_name,
            pe.change_type,
            pe.old_tier,
            pe.new_tier,
            pe.created_at
        FROM perspective_events pe
        LEFT JOIN mcp_server_registry msr ON pe.server_id = msr.server_id
        WHERE pe.perspective_id = :perspective_id
          AND pe.created_at >= :cutoff
        ORDER BY pe.created_at DESC
    """)
    
    result = db.execute(query, {"perspective_id": perspective_id, "cutoff": cutoff})
    rows = result.fetchall()
    
    events = [
        TimelineEvent(
            server_id=row.server_id,
            server_name=row.server_name or "",
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            created_at=row.created_at,
        )
        for row in rows
    ]
    
    return TimelineResponse(
        perspective_id=perspective_id,
        days=days,
        events=events,
    )


if __name__ == "__main__":
    import sys
    from unittest import TestCase
    from fastapi import FastAPI
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    app = FastAPI()
    app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (server_id INTEGER PRIMARY KEY, name TEXT, risk_tier TEXT, confidence REAL, url TEXT, description TEXT, registry_source TEXT, verdict TEXT, verdict_reasoning TEXT, trust_score REAL, scan_count INTEGER, first_seen TEXT, last_seen TEXT, last_scanned TEXT, last_assessed TEXT, meta TEXT)"))
        conn.execute(text("CREATE TABLE perspectives (id INTEGER PRIMARY KEY, name TEXT, description TEXT, org_id INTEGER, created_by INTEGER, facet_filters TEXT, created_at TEXT, updated_at TEXT)"))
        conn.execute(text("CREATE TABLE perspective_events (id INTEGER PRIMARY KEY, perspective_id INTEGER, server_id INTEGER, change_type TEXT, old_tier TEXT, new_tier TEXT, seen INTEGER, created_at TEXT)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, org_id INTEGER, role TEXT, created_at TEXT, password_hash TEXT, clerk_id TEXT, clerk_created_at TEXT, clerk_synced_via TEXT)"))
    
    SessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    session = SessionLocal()
    
    now = datetime.utcnow()
    day1 = now - timedelta(days=1)
    day2 = now - timedelta(days=2)
    
    session.execute(text("INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES (:id, :name, :tier)"), [{"id": 1, "name": "Server Alpha", "tier": "high"}, {"id": 2, "name": "Server Beta", "tier": "low"}])
    session.execute(text("INSERT INTO perspective_events (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at) VALUES (:id, :pid, :sid, :ct, :old, :new, :seen, :ca)"), [
        {"id": 1, "pid": 100, "sid": 1, "ct": "tier_change", "old": "medium", "new": "high", "seen": 1, "ca": day2.isoformat()},
        {"id": 2, "pid": 100, "sid": 2, "ct": "tier_change", "old": "low", "new": "medium", "seen": 1, "ca": day1.isoformat()},
        {"id": 3, "pid": 100, "sid": 1, "ct": "score_update", "old": "low", "new": "high", "seen": 1, "ca": now.isoformat()},
    ])
    session.commit()
    session.close()
    
    client = TestClient(app)
    response = client.get("/api/perspectives/100/timeline?days=7")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    events = data.get("events", [])
    
    assert len(events) >= 2, f"Expected >= 2 events, got {len(events)}"
    
    for i in range(len(events) - 1):
        assert events[i]["created_at"] >= events[i + 1]["created_at"], "Events not ordered by created_at desc"
    
    print("PASS")
    sys.exit(0)