from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import requests
from app.db import get_session
from app.models import AuditLog

router = APIRouter()

class AuditLogRecord(BaseModel):
    timestamp: datetime
    target_server_id: str
    action: str
    actor: str
    org_id: str
    metadata_json: dict

class AuditLogResponse(BaseModel):
    rows: List[AuditLogRecord]
    total: int
    page: int
    page_size: int

def query_audit_logs(
    server_id: Optional[str] = None,
    org_id: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 10,
    session=Depends(get_session)
):
    base_query = session.query(AuditLog)

    if server_id:
        base_query = base_query.filter(AuditLog.target_server_id == server_id)
    if org_id:
        base_query = base_query.filter(AuditLog.org_id == org_id)
    if action:
        base_query = base_query.filter(AuditLog.action == action)
    if start_date:
        base_query = base_query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        base_query = base_query.filter(AuditLog.timestamp <= end_date)

    total = base_query.count()
    offset = (page - 1) * page_size
    rows = base_query.offset(offset).limit(page_size).all()

    return {
        "rows": [{
            "timestamp": row.timestamp,
            "target_server_id": row.target_server_id,
            "action": row.action,
            "actor": row.actor,
            "org_id": row.org_id,
            "metadata_json": row.metadata_json
        } for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size
    }

@router.get("/audit-log", response_model=AuditLogResponse)
async def get_audit_logs(
    server_id: Optional[str] = Query(None),
    org_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    page: int = Query(1),
    page_size: int = Query(10),
):
    try:
        result = query_audit_logs(
            server_id=server_id,
            org_id=org_id,
            action=action,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        AuditLog(
            timestamp=datetime(2023, 1, 1),
            target_server_id="server1",
            action="create",
            actor="user1",
            org_id="org1",
            metadata_json={"key": "value"}
        ),
        AuditLog(
            timestamp=datetime(2023, 1, 2),
            target_server_id="server2",
            action="update",
            actor="user2",
            org_id="org2",
            metadata_json={"key": "value"}
        )
    ])
    test_session.commit()

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/audit-log")
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert len(data["rows"]) == 2
    assert data["rows"][0]["target_server_id"] == "server1"
    assert data["rows"][1]["target_server_id"] == "server2"

    print("PASS")