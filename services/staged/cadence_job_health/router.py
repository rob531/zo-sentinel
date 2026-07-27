from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_daemon_health, get_job_metrics

router = APIRouter(prefix="/api/cadence")

class DaemonHealth(BaseModel):
    name: str
    status: str
    age_seconds: int
    is_stale: bool

class JobMetrics(BaseModel):
    job: str
    total_runs_24h: int
    avg_duration_sec: float
    last_run_at: datetime
    last_status: str

class HealthResponse(BaseModel):
    daemons: List[DaemonHealth]
    jobs: List[JobMetrics]

@router.get("/health", response_model=HealthResponse)
async def get_health(session: Session = Depends(get_session)):
    daemons = get_daemon_health(session)
    jobs = get_job_metrics(session)

    return {
        "daemons": daemons,
        "jobs": jobs
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session as original_get_session

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[original_get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        from app.models import CadenceJobRun, ServiceHealth
        from datetime import datetime, timedelta

        # Seed cadence_job_runs
        session.execute(
            CadenceJobRun.__table__.insert(),
            [
                {"job": "job1", "status": "success", "started_at": datetime.now() - timedelta(hours=1), "finished_at": datetime.now() - timedelta(minutes=30), "rows_affected": 100, "detail": {}},
                {"job": "job2", "status": "success", "started_at": datetime.now() - timedelta(hours=2), "finished_at": datetime.now() - timedelta(hours=1, minutes=30), "rows_affected": 200, "detail": {}},
                {"job": "job1", "status": "failed", "started_at": datetime.now() - timedelta(days=2), "finished_at": datetime.now() - timedelta(days=2, hours=1), "rows_affected": 0, "detail": {}}
            ]
        )

        # Seed service_health
        session.execute(
            ServiceHealth.__table__.insert(),
            [
                {"service": "daemon1", "status": "healthy", "last_heartbeat": datetime.now() - timedelta(minutes=5)},
                {"service": "daemon2", "status": "stale", "last_heartbeat": datetime.now() - timedelta(hours=3)}
            ]
        )
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/cadence/health")
    assert response.status_code == 200
    data = response.json()
    assert len(data["daemons"]) >= 2
    assert len(data["jobs"]) >= 1
    print("PASS")