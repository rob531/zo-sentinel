from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, and_, or_
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db import get_session
from app.models import McpServerRegistry

app = FastAPI()

class HealthSummary(BaseModel):
    total_servers: int
    servers_by_source: Dict[str, int]
    servers_by_risk_tier: Dict[str, int]
    avg_scan_count: float
    servers_scanned_last_24h: int
    servers_not_scanned_7d: int
    oldest_last_scanned: Optional[str]
    newest_last_scanned: Optional[str]

    class Config:
        from_attributes = True

def compute_health_summary(session: Session) -> HealthSummary:
    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    
    total_servers = session.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    
    source_counts = session.query(
        McpServerRegistry.registry_source,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.registry_source).all()
    servers_by_source = {src: count for src, count in source_counts}
    
    tier_counts = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.risk_tier).all()
    servers_by_risk_tier = {tier: count for tier, count in tier_counts}
    
    avg_scan = session.query(func.avg(McpServerRegistry.scan_count)).scalar()
    avg_scan_count = float(avg_scan) if avg_scan else 0.0
    
    servers_scanned_last_24h = session.query(func.count(McpServerRegistry.server_id)).filter(
        McpServerRegistry.last_scanned >= last_24h
    ).scalar() or 0
    
    servers_not_scanned_7d = session.query(func.count(McpServerRegistry.server_id)).filter(
        or_(
            McpServerRegistry.last_scanned < last_7d,
            McpServerRegistry.last_scanned == None
        )
    ).scalar() or 0
    
    oldest_row = session.query(McpServerRegistry.last_scanned).filter(
        McpServerRegistry.last_scanned != None
    ).order_by(McpServerRegistry.last_scanned.asc()).first()
    oldest_last_scanned = oldest_row[0].isoformat() if oldest_row else None
    
    newest_row = session.query(McpServerRegistry.last_scanned).filter(
        McpServerRegistry.last_scanned != None
    ).order_by(McpServerRegistry.last_scanned.desc()).first()
    newest_last_scanned = newest_row[0].isoformat() if newest_row else None
    
    return HealthSummary(
        total_servers=total_servers,
        servers_by_source=servers_by_source,
        servers_by_risk_tier=servers_by_risk_tier,
        avg_scan_count=avg_scan_count,
        servers_scanned_last_24h=servers_scanned_last_24h,
        servers_not_scanned_7d=servers_not_scanned_7d,
        oldest_last_scanned=oldest_last_scanned,
        newest_last_scanned=newest_last_scanned
    )

@app.get("/api/registry/health-summary", response_model=HealthSummary)
def get_health_summary(session: Session = Depends(get_session)):
    return compute_health_summary(session)

def create_app() -> FastAPI:
    return app

def run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    db = TestingSessionLocal()
    now = datetime.utcnow()
    
    servers = [
        McpServerRegistry(server_id="srv1", name="Server 1", registry_source="source_a", risk_tier="low", scan_count=5, last_scanned=now - timedelta(hours=12)),
        McpServerRegistry(server_id="srv2", name="Server 2", registry_source="source_a", risk_tier="medium", scan_count=3, last_scanned=now - timedelta(hours=2)),
        McpServerRegistry(server_id="srv3", name="Server 3", registry_source="source_b", risk_tier="high", scan_count=7, last_scanned=now - timedelta(days=8)),
        McpServerRegistry(server_id="srv4", name="Server 4", registry_source="source_c", risk_tier="low", scan_count=2, last_scanned=now - timedelta(hours=6)),
        McpServerRegistry(server_id="srv5", name="Server 5", registry_source="source_c", risk_tier="critical", scan_count=10, last_scanned=now - timedelta(days=10)),
    ]
    
    for srv in servers:
        db.add(srv)
    db.commit()
    db.close()
    
    response = client.get("/api/registry/health-summary")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert len(data["servers_by_source"]) == 3, f"Expected 3 sources, got {len(data['servers_by_source'])}"
    assert data["total_servers"] == 5, f"Expected 5 servers, got {data['total_servers']}"
    
    app.dependency_overrides.clear()
    print("PASS")

if __name__ == "__main__":
    run_self_test()