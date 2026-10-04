"""
Audit Log Query Service Contract

GET /api/audit/log - Query audit logs with optional filters
"""
from fastapi import FastAPI, Depends, Query
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# Pydantic models for response
class AuditLogRow(BaseModel):
    id: int
    timestamp: datetime
    action: str
    target_server_id: Optional[str] = None
    target_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    detail: Optional[dict] = None
    source_ip: Optional[str] = None

class AuditLogResponse(BaseModel):
    total: int
    rows: List[AuditLogRow]

# FastAPI app
app = FastAPI()

@app.get("/api/audit/log", response_model=AuditLogResponse)
async def get_audit_logs(
    db: Session = Depends(__import__('app.db', fromlist=['get_session']).get_session),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    target_server_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    action: Optional[str] = None,
    from_datetime: Optional[datetime] = Query(default=None, alias="from"),
    to_datetime: Optional[datetime] = Query(default=None, alias="to"),
):
    """
    Query audit logs with optional filters.
    
    Query parameters:
    - limit: Maximum number of rows to return (default: 50)
    - offset: Number of rows to skip (default: 0)
    - target_server_id: Filter by target server ID
    - target_user_id: Filter by target user ID
    - action: Filter by action type
    - from: Start of date range (ISO format)
    - to: End of date range (ISO format)
    """
    # Build parameterized WHERE clause
    conditions = []
    params = {}
    
    if target_server_id is not None:
        conditions.append("target_server_id = :target_server_id")
        params["target_server_id"] = target_server_id
    
    if target_user_id is not None:
        conditions.append("target_user_id = :target_user_id")
        params["target_user_id"] = target_user_id
    
    if action is not None:
        conditions.append("action = :action")
        params["action"] = action
    
    if from_datetime is not None:
        conditions.append("timestamp >= :from_datetime")
        params["from_datetime"] = from_datetime.isoformat()
    
    if to_datetime is not None:
        conditions.append("timestamp <= :to_datetime")
        params["to_datetime"] = to_datetime.isoformat()
    
    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
    
    # Get total count
    count_query = text(f"SELECT COUNT(*) as cnt FROM audit_log{where_clause}")
    total_result = db.execute(count_query, params).fetchone()
    total = total_result[0] if total_result else 0
    
    # Get paginated rows
    params["limit"] = limit
    params["offset"] = offset
    data_query = text(f"""
        SELECT id, timestamp, action, target_server_id, target_user_id, 
               actor_email, detail, source_ip
        FROM audit_log{where_clause}
        ORDER BY timestamp DESC
        LIMIT :limit OFFSET :offset
    """)
    
    rows_data = db.execute(data_query, params).fetchall()
    
    rows = []
    for row in rows_data:
        detail_val = row[6]
        if isinstance(detail_val, str):
            try:
                detail_val = __import__('json').loads(detail_val)
            except Exception:
                pass
        
        rows.append(AuditLogRow(
            id=row[0],
            timestamp=row[1] if isinstance(row[1], datetime) else datetime.fromisoformat(str(row[1])),
            action=row[2],
            target_server_id=row[3],
            target_user_id=row[4],
            actor_email=row[5],
            detail=detail_val,
            source_ip=row[7]
        ))
    
    return AuditLogResponse(total=total, rows=rows)

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}

def verify_contract_integrity():
    """Verify that the contract is properly structured."""
    required_routes = ["/api/audit/log", "/health"]
    routes = [route.path for route in app.routes]
    for required in required_routes:
        assert required in routes, f"Missing required route: {required}"
    return True

if __name__ == "__main__":
    # Self-test with in-memory SQLite
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                target_server_id TEXT,
                target_user_id TEXT,
                actor_org_id TEXT,
                actor_email TEXT,
                detail TEXT,
                source_ip TEXT
            )
        """))
        
        # Seed 3 audit rows across 2 servers
        conn.execute(text("""
            INSERT INTO audit_log (timestamp, action, target_server_id, target_user_id, actor_org_id, actor_email, detail, source_ip)
            VALUES
            ('2024-01-15T10:30:00', 'user_login', 'server-001', 'user-123', 'org-001', 'admin@example.com', '{"ip": "192.168.1.1", "browser": "Chrome"}', '192.168.1.1'),
            ('2024-01-15T11:00:00', 'file_access', 'server-001', 'user-456', 'org-001', 'user@example.com', '{"file": "report.pdf", "operation": "read"}', '192.168.1.2'),
            ('2024-01-15T12:00:00', 'config_change', 'server-002', 'user-789', 'org-002', 'sysadmin@example.com', '{"setting": "timeout", "old_value": 30, "new_value": 60}', '10.0.0.1')
        """))
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    from fastapi.testclient import TestClient
    test_app = FastAPI()
    test_app.include_router(app.router)
    test_app.dependency_overrides[__import__('app.db', fromlist=['get_session']).get_session] = override_get_session
    
    client = TestClient(test_app)
    
    response = client.get("/api/audit/log?limit=50&offset=0")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert data.get("total", 0) >= 3, f"Expected total >= 3, got {data.get('total')}"
    assert len(data.get("rows", [])) > 0, "Expected at least one row"
    
    first_row = data["rows"][0]
    required_fields = ["id", "timestamp", "action", "target_server_id", "target_user_id", "actor_email", "source_ip"]
    for field in required_fields:
        assert field in first_row, f"Missing required field: {field}"
    
    print("PASS")