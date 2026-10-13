from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


app = FastAPI(title="score_dispute_backlog", version="1.0.0")


class DisputeItem(BaseModel):
    id: int
    server_id: int
    server_name: Optional[str]
    status: str
    reason_category: Optional[str]
    created_at: datetime
    submitted_by: str


class BacklogResponse(BaseModel):
    summary: Dict[str, Any]
    disputes: List[DisputeItem]


@app.get("/api/disputes/backlog", response_model=BacklogResponse)
def get_backlog(session: Session = Depends(get_session)) -> BacklogResponse:
    cutoff = datetime.utcnow() - timedelta(days=90)
    
    query = text("""
        SELECT 
            d.id,
            d.server_id,
            s.name as server_name,
            d.status,
            d.reason_category,
            d.created_at,
            d.submitted_by
        FROM mcp_score_disputes d
        LEFT JOIN mcp_server_registry s ON d.server_id = s.server_id
        WHERE d.created_at >= :cutoff
        ORDER BY d.created_at DESC
    """)
    
    result = session.execute(query, {"cutoff": cutoff})
    rows = result.fetchall()
    
    disputes = [
        DisputeItem(
            id=row.id,
            server_id=row.server_id,
            server_name=row.server_name,
            status=row.status,
            reason_category=row.reason_category,
            created_at=row.created_at,
            submitted_by=row.submitted_by
        )
        for row in rows
    ]
    
    total = len(disputes)
    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    
    for d in disputes:
        by_status[d.status] = by_status.get(d.status, 0) + 1
        reason = d.reason_category if d.reason_category else "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    
    return BacklogResponse(
        summary={
            "total": total,
            "by_status": by_status,
            "by_reason": by_reason
        },
        disputes=disputes
    )


def run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
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
                scan_count INTEGER DEFAULT 0,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_score_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason_category TEXT,
                submitted_by TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                explanation TEXT,
                proposed_axes TEXT,
                proposed_overall_risk TEXT,
                admin_note TEXT,
                resolved_at TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))
        conn.commit()
    
    with SessionLocal() as session:
        servers = [
            {"server_id": 1, "name": "server-alpha"},
            {"server_id": 2, "name": "server-beta"},
            {"server_id": 3, "name": "server-gamma"},
        ]
        for s in servers:
            session.execute(
                text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:server_id, :name)"),
                s
            )
        
        now = datetime.utcnow()
        disputes = [
            {"server_id": 1, "status": "open", "reason_category": "scoring_error", "submitted_by": "user1", "created_at": now},
            {"server_id": 2, "status": "resolved", "reason_category": "data_freshness", "submitted_by": "user2", "created_at": now},
            {"server_id": 3, "status": "pending_review", "reason_category": "scoring_error", "submitted_by": "user3", "created_at": now},
            {"server_id": 1, "status": "open", "reason_category": "data_freshness", "submitted_by": "user4", "created_at": now},
            {"server_id": 2, "status": "resolved", "reason_category": "scoring_error", "submitted_by": "user5", "created_at": now},
        ]
        for d in disputes:
            session.execute(
                text("INSERT INTO mcp_score_disputes (server_id, status, reason_category, submitted_by, created_at) VALUES (:server_id, :status, :reason_category, :submitted_by, :created_at)"),
                d
            )
        session.commit()
    
    def override_get_session():
        return SessionLocal()
    
    test_app = FastAPI()
    test_app.include_router(app.router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/api/disputes/backlog")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    assert data["summary"]["total"] == 5, f"Expected total 5, got {data['summary']['total']}"
    
    by_status = data["summary"]["by_status"]
    assert by_status.get("open", 0) == 2, f"Expected 2 open, got {by_status.get('open', 0)}"
    assert by_status.get("resolved", 0) == 2, f"Expected 2 resolved, got {by_status.get('resolved', 0)}"
    assert by_status.get("pending_review", 0) == 1, f"Expected 1 pending_review, got {by_status.get('pending_review', 0)}"
    
    assert len(data["disputes"]) == 5
    
    print("PASS")


if __name__ == "__main__":
    run_self_test()