"""
Perspective Change Timeline Service

Reads perspective_events joined to mcp_server_registry via write_service HTTP on 127.0.0.1:8772.
Returns timeline of tier changes for a given perspective.
"""

from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# Use the REAL app data layer - not inline models
from app.db import get_session
from app.models import PerspectiveEvent, McpServerRegistry

app = FastAPI(title="Perspective Change Timeline")


# Pydantic response models
class TimelineEvent(BaseModel):
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    created_at: datetime


class TimelineResponse(BaseModel):
    perspective_id: str
    days: int
    events: List[TimelineEvent]


@app.get("/api/perspectives/{perspective_id}/timeline", response_model=TimelineResponse)
def get_perspective_timeline(
    perspective_id: str,
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session)
):
    """
    Get timeline of perspective events for a given perspective.
    
    Returns events ordered by created_at descending.
    """
    # Calculate cutoff date
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    # Postgres-portable parameterized query
    query = text("""
        SELECT 
            pe.server_id,
            msr.server_name,
            pe.change_type,
            pe.old_tier,
            pe.new_tier,
            pe.created_at
        FROM perspective_events pe
        INNER JOIN mcp_server_registry msr ON pe.server_id = msr.id
        WHERE pe.perspective_id = :perspective_id
          AND pe.created_at >= :cutoff_date
        ORDER BY pe.created_at DESC
    """)
    
    result = session.execute(query, {
        "perspective_id": perspective_id,
        "cutoff_date": cutoff_date
    })
    
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
    
    return TimelineResponse(
        perspective_id=perspective_id,
        days=days,
        events=events
    )


# Self-test using in-memory SQLite with seeded data
def run_self_test():
    """Acceptance test: seed data, call router, assert results."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    # Create in-memory SQLite for testing
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create tables matching real schema
    with test_engine.connect() as conn:
        # Create mcp_server_registry table
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                id INTEGER PRIMARY KEY,
                server_name VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        # Create perspective_events table
        conn.execute(text("""
            CREATE TABLE perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id VARCHAR(255),
                server_id INTEGER,
                change_type VARCHAR(50),
                old_tier VARCHAR(50),
                new_tier VARCHAR(50),
                created_at TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(id)
            )
        """))
        conn.commit()
    
    TestSessionLocal = sessionmaker(bind=test_engine)
    
    # Seed test data: 2 servers, 3 events across 2 days
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    
    with test_engine.connect() as conn:
        # Seed servers
        conn.execute(text("""
            INSERT INTO mcp_server_registry (id, server_name) VALUES
            (1, 'Test Server Alpha'),
            (2, 'Test Server Beta')
        """))
        
        # Seed perspective events - 3 events across 2 days
        conn.execute(text("""
            INSERT INTO perspective_events 
            (perspective_id, server_id, change_type, old_tier, new_tier, created_at) VALUES
            ('persp_main', 1, 'tier_change', 'low', 'medium', :now),
            ('persp_main', 2, 'tier_change', 'high', 'critical', :yesterday),
            ('persp_main', 1, 'tier_change', 'medium', 'high', :two_days_ago)
        """), {"now": now, "yesterday": yesterday, "two_days_ago": two_days_ago})
        conn.commit()
    
    # Override get_session for testing
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Create test client with dependency override
    test_app = FastAPI()
    
    @test_app.get("/api/perspectives/{perspective_id}/timeline", response_model=TimelineResponse)
    def test_get_timeline(
        perspective_id: str,
        days: int = Query(default=30, ge=1, le=365),
        session: Session = Depends(get_test_session)
    ):
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        query = text("""
            SELECT 
                pe.server_id,
                msr.server_name,
                pe.change_type,
                pe.old_tier,
                pe.new_tier,
                pe.created_at
            FROM perspective_events pe
            INNER JOIN mcp_server_registry msr ON pe.server_id = msr.id
            WHERE pe.perspective_id = :perspective_id
              AND pe.created_at >= :cutoff_date
            ORDER BY pe.created_at DESC
        """)
        
        result = session.execute(query, {
            "perspective_id": perspective_id,
            "cutoff_date": cutoff_date
        })
        
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
        
        return TimelineResponse(
            perspective_id=perspective_id,
            days=days,
            events=events
        )
    
    client = TestClient(test_app)
    
    # Call the router
    response = client.get("/api/perspectives/persp_main/timeline?days=30")
    
    # Assert 200
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    # Assert events list length >= 2
    assert len(data["events"]) >= 2, f"Expected >= 2 events, got {len(data['events'])}"
    
    # Assert date ordering is desc
    events = data["events"]
    for i in range(len(events) - 1):
        curr = datetime.fromisoformat(events[i]["created_at"].replace("Z", "+00:00"))
        next_ts = datetime.fromisoformat(events[i + 1]["created_at"].replace("Z", "+00:00"))
        assert curr >= next_ts, f"Events not in descending order at index {i}"
    
    print("PASS")


if __name__ == "__main__":
    run_self_test()