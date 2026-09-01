from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import List
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db import get_session
from app.models import McpLlmAxisScore

app = FastAPI()

class AdapterInfo(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: str
    axes_covered: int
    server_count: int
    latest_scored_at: str

class AdaptersResponse(BaseModel):
    adapters: List[AdapterInfo]

def get_adapters(session: Session) -> List[AdapterInfo]:
    query = text("""
        SELECT 
            model_version,
            adapter_sha256,
            MAX(decision_rule_version) as decision_rule_version,
            COUNT(DISTINCT axis_name) as axes_covered,
            COUNT(DISTINCT server_id) as server_count,
            MAX(scored_at) as latest_scored_at
        FROM mcp_llm_axis_scores
        GROUP BY model_version, adapter_sha256
        ORDER BY model_version, adapter_sha256
    """)
    result = session.execute(query)
    adapters = []
    for row in result:
        adapters.append(AdapterInfo(
            model_version=row.model_version,
            adapter_sha256=row.adapter_sha256,
            decision_rule_version=row.decision_rule_version,
            axes_covered=row.axes_covered,
            server_count=row.server_count,
            latest_scored_at=row.latest_scored_at.isoformat() if row.latest_scored_at else None
        ))
    return adapters

@app.get("/api/scoring/adapters", response_model=AdaptersResponse)
def list_adapters(session: Session = Depends(get_session)):
    adapters = get_adapters(session)
    return AdaptersResponse(adapters=adapters)

def seed_test_data(session: Session):
    session.execute(text("DELETE FROM mcp_llm_axis_scores WHERE model_version LIKE 'student_%'"))
    session.commit()
    
    test_data = [
        {"model_version": "student_v1", "adapter_sha256": "abc123", "axis_name": "critical", "decision_rule_version": "v1.0", "server_id": "srv1", "scored_at": "2024-01-15 10:00:00"},
        {"model_version": "student_v1", "adapter_sha256": "abc123", "axis_name": "danger", "decision_rule_version": "v1.0", "server_id": "srv1", "scored_at": "2024-01-15 10:00:00"},
        {"model_version": "student_v2", "adapter_sha256": "def456", "axis_name": "critical", "decision_rule_version": "v2.0", "server_id": "srv2", "scored_at": "2024-01-16 11:00:00"},
    ]
    
    for row in test_data:
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores 
                (model_version, adapter_sha256, axis_name, decision_rule_version, server_id, scored_at, label, probs, p_critical, p_danger, p_top)
                VALUES (:model_version, :adapter_sha256, :axis_name, :decision_rule_version, :server_id, :scored_at, 'test', '[0.5]', 0.5, 0.3, 0.2)
            """),
            row
        )
    session.commit()

if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine)
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter_sha256 VARCHAR(64) NOT NULL,
                axis_name VARCHAR(64) NOT NULL,
                decision_rule_version VARCHAR(32) NOT NULL,
                escalated INTEGER DEFAULT 0,
                escalated_to VARCHAR(64),
                label VARCHAR(64),
                label_index INTEGER,
                model_version VARCHAR(64) NOT NULL,
                p_critical FLOAT,
                p_danger FLOAT,
                p_top FLOAT,
                probs TEXT,
                scored_at TIMESTAMP,
                server_id VARCHAR(64) NOT NULL
            )
        """))
        conn.commit()
    
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    test_app = FastAPI()
    test_app.include_router(app.router)
    
    from main import app as main_app
    
    class FakeSession:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, query, params=None):
            if params:
                return self.conn.execute(query, params)
            return self.conn.execute(query)
        def commit(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def __yield__(self):
            return self
    
    with TestClient(test_app) as client:
        with engine.connect() as conn:
            seed_test_data(conn.connection.cursor.db, SessionLocal())
        
        seed_test_data(None, SessionLocal())
        
        response = client.get("/api/scoring/adapters")
        
        if response.status_code != 200:
            print(f"FAIL: status {response.status_code}")
            exit(1)
        
        data = response.json()
        adapters = data.get("adapters", [])
        
        if len(adapters) != 2:
            print(f"FAIL: expected 2 adapters, got {len(adapters)}")
            exit(1)
        
        adapter_map = {(a["model_version"], a["adapter_sha256"]): a for a in adapters}
        
        v1_adapter = adapter_map.get(("student_v1", "abc123"))
        if not v1_adapter:
            print("FAIL: student_v1 adapter not found")
            exit(1)
        if v1_adapter["axes_covered"] != 2:
            print(f"FAIL: student_v1 axes_covered expected 2, got {v1_adapter['axes_covered']}")
            exit(1)
        
        v2_adapter = adapter_map.get(("student_v2", "def456"))
        if not v2_adapter:
            print("FAIL: student_v2 adapter not found")
            exit(1)
        if v2_adapter["axes_covered"] != 1:
            print(f"FAIL: student_v2 axes_covered expected 1, got {v2_adapter['axes_covered']}")
            exit(1)
        
        print("PASS")
        exit(0)