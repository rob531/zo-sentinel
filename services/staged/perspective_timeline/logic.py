from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, select, and_
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import PerspectiveEvent, McpServerRegistry


class TimelineEvent(BaseModel):
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[int]
    new_tier: Optional[int]
    created_at: datetime


class TimelineResponse(BaseModel):
    perspective_id: int
    events: List[TimelineEvent]


def get_perspective_timeline(
    perspective_id: int,
    session: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> TimelineResponse:
    query = (
        select(
            PerspectiveEvent.server_id,
            McpServerRegistry.name.label("server_name"),
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.created_at
        )
        .join(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.server_id
        )
        .where(PerspectiveEvent.perspective_id == perspective_id)
    )
    
    if start_date is not None:
        query = query.where(PerspectiveEvent.created_at >= start_date)
    if end_date is not None:
        query = query.where(PerspectiveEvent.created_at <= end_date)
    
    query = query.order_by(PerspectiveEvent.created_at)
    
    result = session.execute(query).fetchall()
    
    events = [
        TimelineEvent(
            server_id=row.server_id,
            server_name=row.server_name,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            created_at=row.created_at
        )
        for row in result
    ]
    
    return TimelineResponse(perspective_id=perspective_id, events=events)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import text, table, column
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)
    
    with test_engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE perspectives (
                id INTEGER PRIMARY KEY,
                name TEXT,
                description TEXT,
                org_id INTEGER,
                created_by INTEGER,
                facet_filters TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER,
                server_id INTEGER,
                change_type TEXT,
                old_tier INTEGER,
                new_tier INTEGER,
                seen BOOLEAN,
                created_at TIMESTAMP
            )
        """))
        conn.commit()
        
        base_dt = datetime(2024, 1, 1, 12, 0, 0)
        
        conn.execute(
            text("INSERT INTO mcp_server_registry (server_id, name, url) VALUES (:s, :n, :u)"),
            [{"s": 1, "n": "Server Alpha", "u": "http://alpha.example.com"},
             {"s": 2, "n": "Server Beta", "u": "http://beta.example.com"},
             {"s": 3, "n": "Server Gamma", "u": "http://gamma.example.com"}]
        )
        conn.execute(
            text("INSERT INTO perspectives (id, name, org_id, created_by) VALUES (:i, :n, :o, :c)"),
            [{"i": 1, "n": "Perspective 1", "o": 1, "c": 1},
             {"i": 2, "n": "Perspective 2", "o": 1, "c": 1}]
        )
        conn.execute(
            text("INSERT INTO perspective_events (id, perspective_id, server_id, change_type, created_at) VALUES (:i, :p, :s, :c, :t)"),
            [
                {"i": 1, "p": 1, "s": 1, "c": "created", "t": base_dt.isoformat()},
                {"i": 2, "p": 1, "s": 2, "c": "updated", "t": (base_dt + timedelta(hours=1)).isoformat()},
                {"i": 3, "p": 1, "s": 1, "c": "deleted", "t": (base_dt + timedelta(hours=2)).isoformat()},
                {"i": 4, "p": 2, "s": 3, "c": "created", "t": (base_dt + timedelta(days=1)).isoformat()},
                {"i": 5, "p": 2, "s": 2, "c": "updated", "t": (base_dt + timedelta(days=1, hours=1)).isoformat()},
                {"i": 6, "p": 2, "s": 3, "c": "deleted", "t": (base_dt + timedelta(days=1, hours=2)).isoformat()},
            ]
        )
        conn.commit()
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app = FastAPI()
    
    @app.get("/api/perspectives/{perspective_id}/timeline")
    def timeline_endpoint(perspective_id: int, session: Session = Depends(get_session)):
        return get_perspective_timeline(perspective_id, session)
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/api/perspectives/1/timeline")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["events"]) >= 3, f"Expected >= 3 events, got {len(data['events'])}"
    
    first = data["events"][0]
    assert "server_id" in first
    assert "server_name" in first
    assert "change_type" in first
    assert "old_tier" in first
    assert "new_tier" in first
    assert "created_at" in first
    
    print("PASS")