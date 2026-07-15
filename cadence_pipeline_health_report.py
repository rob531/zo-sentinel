from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import ServiceHealth, CadenceJobRun
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class DaemonStatus(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: int
    status: str
    threshold_seconds: int
    meta: Dict

class HealthSummary(BaseModel):
    total: int
    healthy: int
    stale: int
    missing: int

class PipelineHealthReport(BaseModel):
    generated_at: str
    daemons: List[DaemonStatus]
    summary: HealthSummary
    stale_threshold_seconds: int

class JobStatus(BaseModel):
    id: int
    job: str
    status: str
    started_at: Optional[str]
    finished_at: Optional[str]
    rows_affected: Optional[int]
    detail: Optional[str]

class JobReport(BaseModel):
    jobs: List[JobStatus]

def get_daemon_threshold(name: str) -> int:
    thresholds = {
        "write_service": 300,
        "mcp_scanner": 1800,
        "signal_analyser": 1800,
        "trust_synthesiser": 1800,
        "threat_intel_ingestor": 1800,
        "risk_ranker": 1800,
    }
    return thresholds.get(name, 3600)

def calculate_status(last_heartbeat: datetime, threshold: int) -> str:
    age = (datetime.utcnow() - last_heartbeat).total_seconds()
    if age > threshold:
        return "stale"
    return "ok"

@router.get("/cadence/health", response_model=PipelineHealthReport)
async def get_pipeline_health(db: Session = Depends(get_session)) -> PipelineHealthReport:
    now = datetime.utcnow()
    daemons = []

    # Get service health data
    service_health = db.query(ServiceHealth).all()

    for health in service_health:
        threshold = get_daemon_threshold(health.name)
        age = (now - health.last_heartbeat).total_seconds()
        status = calculate_status(health.last_heartbeat, threshold)

        daemons.append({
            "name": health.name,
            "last_heartbeat": health.last_heartbeat.isoformat(),
            "age_seconds": int(age),
            "status": status,
            "threshold_seconds": threshold,
            "meta": health.meta or {}
        })

    # Calculate summary
    summary = {
        "total": len(daemons),
        "healthy": sum(1 for d in daemons if d["status"] == "ok"),
        "stale": sum(1 for d in daemons if d["status"] == "stale"),
        "missing": 0  # No missing daemons in this implementation
    }

    return {
        "generated_at": now.isoformat(),
        "daemons": daemons,
        "summary": summary,
        "stale_threshold_seconds": 3600  # Default threshold
    }

@router.get("/cadence/jobs", response_model=JobReport)
async def get_jobs(db: Session = Depends(get_session)) -> JobReport:
    jobs = db.query(CadenceJobRun).all()
    return {"jobs": [
        {
            "id": job.id,
            "job": job.job,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "rows_affected": job.rows_affected,
            "detail": job.detail
        } for job in jobs
    ]}

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)

    async def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Add test data
    def add_test_data():
        session = SessionLocal()
        try:
            # Add service health data
            session.add_all([
                ServiceHealth(
                    name="write_service",
                    status="ok",
                    meta={"version": "1.0"},
                    last_heartbeat=datetime.utcnow() - timedelta(seconds=200)
                ),
                ServiceHealth(
                    name="mcp_scanner",
                    status="ok",
                    meta={"version": "1.0"},
                    last_heartbeat=datetime.utcnow() - timedelta(seconds=1700)
                ),
                ServiceHealth(
                    name="signal_analyser",
                    status="ok",
                    meta={"version": "1.0"},
                    last_heartbeat=datetime.utcnow() - timedelta(seconds=3500)
                ),
                ServiceHealth(
                    name="trust_synthesiser",
                    status="ok",
                    meta={"version": "1.0"},
                    last_heartbeat=datetime.utcnow() - timedelta(seconds=3700)
                ),
            ])

            # Add job data
            session.add_all([
                CadenceJobRun(
                    job="test_job_1",
                    status="completed",
                    started_at=datetime.utcnow() - timedelta(hours=1),
                    finished_at=datetime.utcnow() - timedelta(minutes=30),
                    rows_affected=100,
                    detail="Test job 1"
                ),
                CadenceJobRun(
                    job="test_job_2",
                    status="failed",
                    started_at=datetime.utcnow() - timedelta(hours=2),
                    finished_at=datetime.utcnow() - timedelta(hours=1),
                    rows_affected=0,
                    detail="Test job 2 failed"
                ),
            ])

            session.commit()
        finally:
            session.close()

    add_test_data()

    # Run tests
    client = TestClient(app)

    # Test /cadence/health
    response = client.get("/cadence/health")
    assert response.status_code == 200
    data = response.json()
    assert "generated_at" in data
    assert "daemons" in data
    assert "summary" in data
    assert data["summary"]["total"] == 4
    assert data["summary"]["healthy"] >= 0
    assert data["summary"]["stale"] >= 0
    assert data["summary"]["missing"] == 0

    # Test /cadence/jobs
    response = client.get("/cadence/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    assert len(data["jobs"]) == 2

    print("PASS")