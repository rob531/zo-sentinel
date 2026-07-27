from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pydantic import BaseModel
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import CadenceJobRun, ServiceHealth

class JobStatus(BaseModel):
    job: str
    total_runs_24h: int
    avg_duration_sec: Optional[float]
    last_run_at: Optional[datetime]
    last_status: Optional[str]

class DaemonStatus(BaseModel):
    name: str
    status: str
    age_seconds: int
    is_stale: bool

class HealthResponse(BaseModel):
    daemons: List[DaemonStatus]
    jobs: List[JobStatus]

def get_daemon_statuses(session: Session) -> List[DaemonStatus]:
    now = datetime.utcnow()
    stale_threshold = timedelta(minutes=5)

    health_records = session.query(ServiceHealth).all()
    daemons = []

    for record in health_records:
        last_heartbeat = record.last_heartbeat
        age = (now - last_heartbeat).total_seconds()
        is_stale = age > stale_threshold.total_seconds()

        daemon = DaemonStatus(
            name=record.service,
            status=record.status,
            age_seconds=int(age),
            is_stale=is_stale
        )
        daemons.append(daemon)

    return daemons

def get_job_statuses(session: Session) -> List[JobStatus]:
    now = datetime.utcnow()
    twenty_four_hours_ago = now - timedelta(hours=24)

    job_runs = session.query(CadenceJobRun).filter(
        CadenceJobRun.started_at >= twenty_four_hours_ago
    ).all()

    job_stats = {}

    for run in job_runs:
        if run.job not in job_stats:
            job_stats[run.job] = {
                'total_runs': 0,
                'total_duration': 0,
                'last_run_at': None,
                'last_status': None
            }

        job_stats[run.job]['total_runs'] += 1
        job_stats[run.job]['total_duration'] += (run.finished_at - run.started_at).total_seconds()

        if run.finished_at > (job_stats[run.job]['last_run_at'] or run.finished_at):
            job_stats[run.job]['last_run_at'] = run.finished_at
            job_stats[run.job]['last_status'] = run.status

    jobs = []
    for job, stats in job_stats.items():
        avg_duration = stats['total_duration'] / stats['total_runs'] if stats['total_runs'] > 0 else None
        jobs.append(JobStatus(
            job=job,
            total_runs_24h=stats['total_runs'],
            avg_duration_sec=avg_duration,
            last_run_at=stats['last_run_at'],
            last_status=stats['last_status']
        ))

    return jobs

def get_cadence_health(session: Session) -> HealthResponse:
    daemons = get_daemon_statuses(session)
    jobs = get_job_statuses(session)
    return HealthResponse(daemons=daemons, jobs=jobs)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create in-memory SQLite database for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Seed service_health data
        session.add_all([
            ServiceHealth(service="daemon1", status="healthy", last_heartbeat=datetime.utcnow()),
            ServiceHealth(service="daemon2", status="running", last_heartbeat=datetime.utcnow() - timedelta(minutes=3)),
            ServiceHealth(service="daemon3", status="stale", last_heartbeat=datetime.utcnow() - timedelta(minutes=10))
        ])

        # Seed cadence_job_runs data
        now = datetime.utcnow()
        session.add_all([
            CadenceJobRun(
                job="job1",
                status="completed",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(minutes=50),
                rows_affected=100,
                detail={}
            ),
            CadenceJobRun(
                job="job1",
                status="completed",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1, minutes=50),
                rows_affected=200,
                detail={}
            ),
            CadenceJobRun(
                job="job2",
                status="failed",
                started_at=now - timedelta(hours=24, minutes=10),
                finished_at=now - timedelta(hours=23, minutes=50),
                rows_affected=0,
                detail={"error": "timeout"}
            )
        ])

        session.commit()

        # Test the logic
        response = get_cadence_health(session)

        # Assertions
        assert len(response.daemons) >= 2
        assert len(response.jobs) >= 1

        print("PASS")
    finally:
        session.close()