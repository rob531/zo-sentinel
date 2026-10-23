from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
import requests
import json

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["high_risk_servers"])


class ServerSummary(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_seen: Optional[str]
    last_scanned: Optional[str]
    scan_count: int
    url: str
    description: Optional[str]


class HighRiskServersResponse(BaseModel):
    servers: List[ServerSummary]
    total: int
    page: int


def query_mesh(query: str, params: dict = None) -> dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params or {}},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {"rows": [], "error": str(Exception)}


@router.get("/servers/high-risk", response_model=HighRiskServersResponse)
def get_high_risk_servers(
    page: int = 1,
    page_size: int = 50,
    session: Session = Depends(get_session)
) -> HighRiskServersResponse:
    offset = (page - 1) * page_size
    
    count_query = text("""
        SELECT COUNT(*) as total
        FROM McpServerRegistry
        WHERE risk_tier IN ('HIGH_RISK_ISOLATED', 'KNOWN_THREAT')
    """)
    total_result = session.execute(count_query).fetchone()
    total = total_result[0] if total_result else 0
    
    data_query = text("""
        SELECT server_id, name, risk_tier, last_seen, last_scanned, scan_count, url, description
        FROM McpServerRegistry
        WHERE risk_tier IN ('HIGH_RISK_ISOLATED', 'KNOWN_THREAT')
        ORDER BY last_seen DESC
        LIMIT :page_size OFFSET :offset
    """)
    rows = session.execute(data_query, {"page_size": page_size, "offset": offset}).fetchall()
    
    servers = [
        ServerSummary(
            server_id=row[0],
            name=row[1],
            risk_tier=row[2],
            last_seen=str(row[3]) if row[3] else None,
            last_scanned=str(row[4]) if row[4] else None,
            scan_count=row[5] or 0,
            url=row[6],
            description=row[7]
        )
        for row in rows
    ]
    
    return HighRiskServersResponse(servers=servers, total=total, page=page)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    in_memory_db = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    create_tables_sql = text("""
        CREATE TABLE IF NOT EXISTS McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            last_seen TEXT,
            last_scanned TEXT,
            scan_count INTEGER DEFAULT 0,
            url TEXT,
            description TEXT
        )
    """)
    
    with in_memory_db.connect() as conn:
        conn.execute(create_tables_sql)
        conn.commit()
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_db)
    
    seed_data = [
        {"server_id": "srv-001", "name": "Safe Server", "risk_tier": "LOW_RISK", "last_seen": "2024-01-15", "last_scanned": "2024-01-14", "scan_count": 10, "url": "http://safe.local", "description": "Safe server"},
        {"server_id": "srv-002", "name": "Threat Server", "risk_tier": "KNOWN_THREAT", "last_seen": "2024-01-16", "last_scanned": "2024-01-15", "scan_count": 5, "url": "http://threat.local", "description": "Known threat"},
        {"server_id": "srv-003", "name": "High Risk Server", "risk_tier": "HIGH_RISK_ISOLATED", "last_seen": "2024-01-17", "last_scanned": "2024-01-16", "scan_count": 3, "url": "http://highrisk.local", "description": "High risk isolated"},
        {"server_id": "srv-004", "name": "Medium Server", "risk_tier": "MEDIUM_RISK", "last_seen": "2024-01-14", "last_scanned": "2024-01-13", "scan_count": 8, "url": "http://medium.local", "description": "Medium risk"},
        {"server_id": "srv-005", "name": "Critical Threat", "risk_tier": "KNOWN_THREAT", "last_seen": "2024-01-18", "last_scanned": "2024-01-17", "scan_count": 2, "url": "http://critical.local", "description": "Critical known threat"},
    ]
    
    insert_sql = text("""
        INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_seen, last_scanned, scan_count, url, description)
        VALUES (:server_id, :name, :risk_tier, :last_seen, :last_scanned, :scan_count, :url, :description)
    """)
    
    with in_memory_db.connect() as conn:
        for row in seed_data:
            conn.execute(insert_sql, row)
        conn.commit()
    
    original_session = None
    patched = {"called": False}
    
    def patched_get_session():
        patched["called"] = True
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    original_requests_post = requests.post
    requests.post = lambda *args, **kwargs: original_requests_post("http://127.0.0.1:8772/query", *args, **kwargs)
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    test_app.dependency_overrides[get_session] = patched_get_session
    
    import uvicorn
    from threading import Thread
    
    server_thread = Thread(
        target=lambda: uvicorn.run(test_app, host="127.0.0.1", port=18773, log_level="error"),
        daemon=True
    )
    server_thread.start()
    import time
    time.sleep(1.5)
    
    try:
        import httpx
        client = httpx.Client(base_url="http://127.0.0.1:18773", timeout=10)
        response = client.get("/api/servers/high-risk")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        servers = data.get("servers", [])
        high_risk_tiers = {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}
        for srv in servers:
            assert srv["risk_tier"] in high_risk_tiers, f"Unexpected tier: {srv['risk_tier']}"
        
        assert len(servers) == 3, f"Expected 3 servers, got {len(servers)}"
        assert data["total"] == 3, f"Expected total 3, got {data['total']}"
        
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        raise
    finally:
        requests.post = original_requests_post