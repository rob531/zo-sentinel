"""
Audit Log API Contract
GET /api/audit/log - Retrieve audit log entries with server name resolution
"""

from fastapi import FastAPI, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# Import real data layer
from app.db import get_session
from app.models import McpServerRegistry

# Pydantic schemas
class AuditLogRow(BaseModel):
    timestamp: datetime
    target_server_id: int
    action: str
    actor: str
    detail: Optional[str]
    server_name: str

class AuditLogResponse(BaseModel):
    rows: List[AuditLogRow]
    total: int
    limit: int
    offset: int

# FastAPI app
app = FastAPI()

@app.get("/api/audit/log", response_model=AuditLogResponse)
def get_audit_log(
    server_id: Optional[int] = Query(None, description="Filter by server ID"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip first N results"),
    session: Session = Depends(get_session)
):
    """
    Retrieve audit log entries joined with McpServerRegistry for server name resolution.
    Returns rows with timestamp, target_server_id, action, actor, detail, and server_name.
    """
    # Base query with join
    base_query = """
        SELECT 
            al.timestamp,
            al.target_server_id,
            al.action,
            al.actor,
            al.detail,
            msr.server_name
        FROM audit_log al
        LEFT JOIN McpServerRegistry msr ON al.target_server_id = msr.id
        WHERE 1=1
    """
    params = {}
    
    if server_id is not None:
        base_query += " AND al.target_server_id = :server_id"
        params["server_id"] = server_id
    
    # Count query
    count_query = f"SELECT COUNT(*) FROM ({base_query}) as subquery"
    total_result = session.execute(text(count_query), params).scalar()
    
    # Main query with pagination
    paginated_query = f"""
        {base_query}
        ORDER BY al.timestamp DESC
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = limit
    params["offset"] = offset
    
    result = session.execute(text(paginated_query), params)
    rows = [
        AuditLogRow(
            timestamp=row[0],
            target_server_id=row[1],
            action=row[2],
            actor=row[3],
            detail=row[4],
            server_name=row[5] or "unknown"
        )
        for row in result.fetchall()
    ]
    
    return AuditLogResponse(
        rows=rows,
        total=total_result or 0,
        limit=limit,
        offset=offset
    )

def health(session: Session = Depends(get_session)) -> dict:
    """Health check - verify tables exist."""
    session.execute(text("SELECT 1 FROM audit_log LIMIT 1"))
    return {"status": "healthy"}

def log_audit(
    session: Session,
    target_server_id: int,
    action: str,
    actor: str,
    detail: Optional[str] = None,
    timestamp: Optional[datetime] = None
) -> dict:
    """Log an audit entry."""
    if timestamp is None:
        timestamp = datetime.utcnow()
    session.execute(
        text("""
            INSERT INTO audit_log (timestamp, target_server_id, action, actor, detail)
            VALUES (:timestamp, :target_server_id, :action, :actor, :detail)
        """),
        {
            "timestamp": timestamp,
            "target_server_id": target_server_id,
            "action": action,
            "actor": actor,
            "detail": detail
        }
    )
    session.commit()
    return {"status": "logged"}

def ensure_tables(session: Session) -> None:
    """Create required tables if they don't exist."""
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
            target_server_id INTEGER NOT NULL,
            action VARCHAR(255) NOT NULL,
            actor VARCHAR(255) NOT NULL,
            detail TEXT,
            FOREIGN KEY (target_server_id) REFERENCES McpServerRegistry(id) ON DELETE CASCADE
        )
    """))
    session.commit()

def create_entries_bulk(session: Session, entries: List[dict]) -> dict:
    """Bulk create audit log entries."""
    for entry in entries:
        session.execute(
            text("""
                INSERT INTO audit_log (timestamp, target_server_id, action, actor, detail)
                VALUES (:timestamp, :target_server_id, :action, :actor, :detail)
            """),
            entry
        )
    session.commit()
    return {"created": len(entries)}

if __name__ == "__main__":
    # Self-test with SQLite in-memory database
    import os
    import sys
    
    # Create in-memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    # Override dependencies
    that_app = FastAPI()
    that_app.include_router(app.router)
    that_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(that_app)
    
    # Create tables in test DB
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                id INTEGER PRIMARY KEY,
                server_name VARCHAR(255) NOT NULL,
                endpoint VARCHAR(500),
                status VARCHAR(50) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                target_server_id INTEGER NOT NULL,
                action VARCHAR(255) NOT NULL,
                actor VARCHAR(255) NOT NULL,
                detail TEXT,
                FOREIGN KEY (target_server_id) REFERENCES McpServerRegistry(id) ON DELETE CASCADE
            )
        """))
        conn.commit()
    
    # Seed test data: 2 servers, 3 audit rows
    with engine.connect() as conn:
        # Server 1
        conn.execute(
            text("INSERT INTO McpServerRegistry (id, server_name) VALUES (:id, :name)"),
            {"id": 1, "name": "production-api"}
        )
        # Server 2
        conn.execute(
            text("INSERT INTO McpServerRegistry (id, server_name) VALUES (:id, :name)"),
            {"id": 2, "name": "staging-worker"}
        )
        conn.commit()
    
    # Seed audit log entries
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    audit_entries = [
        {
            "timestamp": base_time,
            "target_server_id": 1,
            "action": "deploy",
            "actor": "admin@corp.com",
            "detail": "Version 2.1.0 deployed"
        },
        {
            "timestamp": datetime(2024, 1, 15, 11, 30, 0),
            "target_server_id": 2,
            "action": "scale",
            "actor": "ops@corp.com",
            "detail": "Scaled to 3 replicas"
        },
        {
            "timestamp": datetime(2024, 1, 15, 14, 45, 0),
            "target_server_id": 1,
            "action": "config_update",
            "actor": "dev@corp.com",
            "detail": "Updated timeout settings"
        }
    ]
    
    with engine.connect() as conn:
        for entry in audit_entries:
            conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, target_server_id, action, actor, detail)
                    VALUES (:timestamp, :target_server_id, :action, :actor, :detail)
                """),
                entry
            )
        conn.commit()
    
    # Run acceptance tests
    try:
        # Test 1: Basic endpoint returns 200
        response = client.get("/api/audit/log")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Test 2: Assert rows length (should have 3 entries)
        assert len(data["rows"]) == 3, f"Expected 3 rows, got {len(data['rows'])}"
        
        # Test 3: Assert server name resolution
        server_names = {row["server_name"] for row in data["rows"]}
        assert "production-api" in server_names, "Missing server_name 'production-api'"
        assert "staging-worker" in server_names, "Missing server_name 'staging-worker'"
        
        # Test 4: Verify all expected fields present
        for row in data["rows"]:
            assert "timestamp" in row
            assert "target_server_id" in row
            assert "action" in row
            assert "actor" in row
            assert "detail" in row
            assert "server_name" in row
        
        # Test 5: Total count matches
        assert data["total"] == 3, f"Expected total=3, got {data['total']}"
        
        # Test 6: Filter by server_id
        response = client.get("/api/audit/log?server_id=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["rows"]) == 2, "Server 1 should have 2 entries"
        for row in data["rows"]:
            assert row["target_server_id"] == 1
        
        # Test 7: Pagination
        response = client.get("/api/audit/log?limit=1&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["rows"]) == 1
        assert data["limit"] == 1
        assert data["offset"] == 0
        
        print("PASS")
        sys.exit(0)
        
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        sys.exit(1)