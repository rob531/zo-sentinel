from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from typing import Dict, Any
import requests
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
from pydantic import BaseModel

router = APIRouter()

class DaemonStatus(BaseModel):
    status: str
    last_heartbeat: str
    uptime_seconds: int

class HealthReport(BaseModel):
    sentinel_directive_generator: DaemonStatus
    gate_scheduler: DaemonStatus
    zo_sentinel_builder: DaemonStatus

def get_daemon_status(db: Session, daemon_name: str) -> DaemonStatus:
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.name == daemon_name).first()
    if not server:
        raise HTTPException(status_code=404, detail=f"Daemon {daemon_name} not found")

    now = datetime.utcnow()
    last_heartbeat = server.last_heartbeat
    uptime = (now - server.start_time).total_seconds()
    status = "healthy" if (now - last_heartbeat) < timedelta(minutes=5) else "stale"

    return DaemonStatus(
        status=status,
        last_heartbeat=last_heartbeat.isoformat(),
        uptime_seconds=int(uptime)
    )

@router.get("/health/factory-liveness", response_model=HealthReport)
async def factory_liveness(db: Session = Depends(get_session)) -> HealthReport:
    try:
        sentinel_status = get_daemon_status(db, "sentinel_directive_generator")
        gate_status = get_daemon_status(db, "gate_scheduler")
        builder_status = get_daemon_status(db, "zo_sentinel_builder")

        return HealthReport(
            sentinel_directive_generator=sentinel_status,
            gate_scheduler=gate_status,
            zo_sentinel_builder=builder_status
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(
            name="sentinel_directive_generator",
            start_time=datetime.utcnow() - timedelta(minutes=10),
            last_heartbeat=datetime.utcnow() - timedelta(seconds=30)
        ),
        MCPServerRegistry(
            name="gate_scheduler",
            start_time=datetime.utcnow() - timedelta(minutes=20),
            last_heartbeat=datetime.utcnow() - timedelta(seconds=10)
        ),
        MCPServerRegistry(
            name="zo_sentinel_builder",
            start_time=datetime.utcnow() - timedelta(minutes=30),
            last_heartbeat=datetime.utcnow() - timedelta(seconds=5)
        )
    ])
    test_session.commit()

    client = TestClient(app)
    response = client.get("/health/factory-liveness")
    assert response.status_code == 200
    report = response.json()
    assert all(daemon in report for daemon in ["sentinel_directive_generator", "gate_scheduler", "zo_sentinel_builder"])
    print("PASS")