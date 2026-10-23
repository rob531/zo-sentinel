from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.db import get_session
from app.models import McpServerRegistry
from testclient import TestClient
from sqlalchemy.pool import StaticPool


class AuditLogEntry(BaseModel):
    id: int
    timestamp: datetime
    action: str
    actor: str
    target_type: str
    target_server_id: Optional[str] = None
    server_name: Optional[str] = None
    detail: Optional[dict] = None
    meta: Optional[dict] = None


class AuditLogResponse(BaseModel):
    entries: List[AuditLogEntry]
    total: int
    page: int


def query_audit_logs(
    session: Session,
    org_id: Optional[int] = None,
    target_server_id: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100
) -> tuple[List[dict], int]:
    conditions = []
    params = {}

    if org_id is not None:
        conditions.append("a.org_id = :org_id")
        params["org_id"] = org_id
    if target_server_id is not None:
        conditions.append("a.target_server_id = :target_server_id")
        params["target_server_id"] = target_server_id
    if action is not None:
        conditions.append("a.action = :action")
        params["action"] = action
    if actor is not None:
        conditions.append("a.actor = :actor")
        params["actor"] = actor
    if start_date is not None:
        conditions.append("a.timestamp >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        conditions.append("a.timestamp <= :end_date")
        params["end_date"] = end_date

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    count_sql = text(f"""
        SELECT COUNT(*) FROM audit_log a
        WHERE {where_clause}
    """)
    total = session.execute(count_sql, params).scalar() or 0

    query_sql = text(f"""
        SELECT a.id, a.timestamp, a.action, a.actor, a.target_type,
               a.target_server_id, s.name as server_name, a.detail, a.meta
        FROM audit_log a
        LEFT JOIN mcp_server_registry s ON a.target_server_id = s.server_id
        WHERE {where_clause}
        ORDER BY a.timestamp DESC
        LIMIT :limit
    """)
    params["limit"] = limit

    result = session.execute(query_sql, params)
    entries = []
    for row in result:
        entries.append({
            "id": row.id,
            "timestamp": row.timestamp,
            "action": row.action,
            "actor": row.actor,
            "target_type": row.target_type,
            "target_server_id": row.target_server_id,
            "server_name": row.server_name,
            "detail": row.detail,
            "meta": row.meta
        })
    return entries, total


router = APIRouter()


@router.get("/api/audit/log", response_model=AuditLogResponse)
def get_audit_log(
    org_id: Optional[int] = Query(None),
    target_server_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session)
):
    entries, total = query_audit_logs(
        session=session,
        org_id=org_id,
        target_server_id=target_server_id,
        action=action,
        actor=actor,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )
    return AuditLogResponse(
        entries=[AuditLogEntry(**e) for e in entries],
        total=total,
        page=1
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name)
            VALUES ('srv_001', 'Test Server Alpha')
        """))
        conn.execute(text("""
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_server_id TEXT,
                org_id INTEGER NOT NULL,
                detail TEXT,
                meta TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO audit_log VALUES
            (1, '2025-01-15T10:00:00', 'VERDICT_CHANGE', 'user_1', 'server', 'srv_001', 1, '{"old":"unknown","new":"safe"}', '{}'),
            (2, '2025-01-15T11:00:00', 'SCAN_COMPLETE', 'user_2', 'server', 'srv_001', 1, '{"duration":30}', '{}'),
            (3, '2025-01-15T12:00:00', 'VERDICT_CHANGE', 'user_99', 'server', NULL, 2, '{"old":"unknown","new":"unsafe"}', '{}')
        """))
        conn.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    r1 = client.get("/api/audit/log?target_server_id=srv_001&org_id=1")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 2, f"Expected 2, got {d1['total']}"
    assert len(d1["entries"]) == 2

    r2 = client.get("/api/audit/log?action=VERDICT_CHANGE")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["total"] == 2, f"Expected 2, got {d2['total']}"
    assert len(d2["entries"]) == 2
    assert d2["entries"][0]["actor"] == "user_99"
    assert d2["entries"][0]["server_name"] == "Test Server Alpha"

    print("PASS")