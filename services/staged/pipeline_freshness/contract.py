from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import CadenceJobRun, McpServerRegistry
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Any

router = APIRouter()

@router.get("/api/pipeline/freshness")
def get_pipeline_freshness(db: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    
    total_stmt = select(func.count(McpServerRegistry.server_id))
    servers_total = db.execute(total_stmt).scalar() or 0
    
    never_scanned_stmt = select(func.count(McpServerRegistry.server_id)).where(
        McpServerRegistry.last_scanned.is_(None)
    )
    servers_never_scanned = db.execute(never_scanned_stmt).scalar() or 0
    
    stale_stmt = select(func.count(McpServerRegistry.server_id)).where(
        McpServerRegistry.last_scanned.isnot(None),
        McpServerRegistry.last_scanned < day_ago
    )
    servers_stale_24h = db.execute(stale_stmt).scalar() or 0
    
    last_ingest_stmt = select(CadenceJobRun).where(
        CadenceJobRun.job == 'mcp_ingest'
    ).order_by(CadenceJobRun.started_at.desc()).limit(1)
    last_ingest = db.execute(last_ingest_stmt).scalar_one_or_none()
    
    last_ingest_job = None
    if last_ingest:
        last_ingest_job = {
            "job": last_ingest.job,
            "status": last_ingest.status,
            "started_at": last_ingest.started_at.isoformat() if last_ingest.started_at else None,
            "finished_at": last_ingest.finished_at.isoformat() if last_ingest.finished_at else None,
            "rows_affected": last_ingest.rows_affected,
            "detail": last_ingest.detail
        }
    
    rows_stmt = select(func.coalesce(func.sum(CadenceJobRun.rows_affected), 0)).where(
        CadenceJobRun.job == 'mcp_ingest',
        CadenceJobRun.started_at >= day_ago
    )
    ingest_rows_24h = db.execute(rows_stmt).scalar() or 0
    
    scan_stmt = select(func.max(McpServerRegistry.last_scanned)).where(
        McpServerRegistry.last_scanned.isnot(None)
    )
    last_scan_time = db.execute(scan_stmt).scalar()
    
    scanner_cycle_age_seconds = 0
    if last_scan_time:
        scanner_cycle_age_seconds = int((now - last_scan_time.replace(tzinfo=timezone.utc)).total_seconds())
    
    growth_stmt = select(func.count(McpServerRegistry.server_id)).where(
        McpServerRegistry.first_seen >= day_ago
    )
    registry_growth_24h = db.execute(growth_stmt).scalar() or 0
    
    return {
        "servers_total": servers_total,
        "servers_never_scanned": servers_never_scanned,
        "servers_stale_24h": servers_stale_24h,
        "last_ingest_job": last_ingest_job,
        "ingest_rows_24h": ingest_rows_24h,
        "scanner_cycle_age_seconds": scanner_cycle_age_seconds,
        "registry_growth_24h": registry_growth_24h
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)
    
    client = TestClient(app)
    
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    hour_ago = now - timedelta(hours=1)
    
    db.add(McpServerRegistry(
        server_id="srv_never_scanned",
        name="Never Scanned Server",
        url="http://never.example.com",
        first_seen=day_ago,
        last_seen=day_ago,
        registry_source="test",
        last_scanned=None,
        scan_count=0
    ))
    
    db.add(McpServerRegistry(
        server_id="srv_stale",
        name="Stale Server",
        url="http://stale.example.com",
        first_seen=day_ago,
        last_seen=day_ago,
        registry_source="test",
        last_scanned=day_ago - timedelta(hours=25),
        scan_count=1
    ))
    
    db.add(McpServerRegistry(
        server_id="srv_fresh",
        name="Fresh Server",
        url="http://fresh.example.com",
        first_seen=day_ago,
        last_seen=hour_ago,
        registry_source="test",
        last_scanned=hour_ago,
        scan_count=5
    ))
    
    db.add(CadenceJobRun(
        job="mcp_ingest",
        status="completed",
        started_at=hour_ago,
        finished_at=hour_ago + timedelta(minutes=5),
        rows_affected=150,
        detail="Ingest completed"
    ))
    
    db.add(CadenceJobRun(
        job="mcp_ingest",
        status="failed",
        started_at=day_ago - timedelta(hours=2),
        finished_at=day_ago - timedelta(hours=2) + timedelta(minutes=1),
        rows_affected=0,
        detail="Connection error"
    ))
    
    db.commit()
    db.close()
    
    response = client.get("/api/pipeline/freshness")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert data["servers_never_scanned"] == 1, f"Expected never_scanned=1, got {data['servers_never_scanned']}"
    assert data["servers_stale_24h"] == 1, f"Expected stale_24h=1, got {data['servers_stale_24h']}"
    assert data["servers_total"] == 3
    assert data["last_ingest_job"]["status"] == "completed"
    assert data["ingest_rows_24h"] == 150
    
    print("PASS")