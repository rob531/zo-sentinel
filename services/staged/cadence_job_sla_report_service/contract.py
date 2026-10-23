from datetime import datetime, timedelta
from random import randint, choice
import statistics
import sys

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class JobSLAMetrics(BaseModel):
    name: str
    avg_duration: float
    p95_duration: float
    success_rate: float
    last_run: datetime | None
    next_expected: datetime | None


class SLAReportResponse(BaseModel):
    jobs: list[JobSLAMetrics]
    generated_at: datetime


def compute_sla_metrics(runs: list[CadenceJobRun]) -> dict:
    if not runs:
        return {
            "avg_duration": 0.0,
            "p95_duration": 0.0,
            "success_rate": 0.0,
            "last_run": None,
            "next_expected": None,
        }
    completed = [r for r in runs if r.status == "completed" and r.started_at and r.finished_at]
    started_count = len(runs)
    completed_count = len(completed)

    success_rate = (100.0 * completed_count / started_count) if started_count > 0 else 0.0

    last_run = max((r.started_at for r in runs if r.started_at), default=None)

    durations = []
    if completed:
        for r in completed:
            delta = r.finished_at - r.started_at
            durations.append(delta.total_seconds())

    avg_duration = statistics.mean(durations) if durations else 0.0
    p95_duration = statistics.quantiles(durations, n=20)[18] if len(durations) >= 2 else (durations[0] if durations else 0.0)

    next_expected = None
    if len(runs) >= 2:
        sorted_runs = sorted([r for r in runs if r.started_at], key=lambda r: r.started_at)
        if len(sorted_runs) >= 2:
            last_two = sorted_runs[-2:]
            interval = (last_two[1].started_at - last_two[0].started_at).total_seconds()
            next_expected = last_run + timedelta(seconds=interval) if last_run else None

    return {
        "avg_duration": avg_duration,
        "p95_duration": p95_duration,
        "success_rate": success_rate,
        "last_run": last_run,
        "next_expected": next_expected,
    }


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/cadence/sla-report", response_model=SLAReportResponse)
    def get_sla_report(session: Session = Depends(get_session)):
        runs = session.query(CadenceJobRun).all()
        jobs_map: dict[str, list[CadenceJobRun]] = {}
        for run in runs:
            jobs_map.setdefault(run.job, []).append(run)

        job_metrics = []
        for job_name, job_runs in jobs_map.items():
            metrics = compute_sla_metrics(job_runs)
            job_metrics.append(JobSLAMetrics(name=job_name, **metrics))

        return SLAReportResponse(jobs=job_metrics, generated_at=datetime.utcnow())

    return app


def _seed_data(session: Session):
    session.query(CadenceJobRun).delete()
    session.commit()

    jobs = ["backup_daily", "sync_users", "health_check"]
    base_time = datetime.utcnow() - timedelta(days=7)

    for job in jobs:
        for i in range(10):
            status = choice(["completed", "completed", "completed", "failed"])
            started = base_time + timedelta(hours=i * 2 + randint(0, 30) / 60)
            if status == "completed":
                duration = randint(10, 300)
                finished = started + timedelta(seconds=duration)
                rows = randint(100, 5000)
            else:
                finished = started + timedelta(seconds=randint(5, 50))
                rows = 0
            run = CadenceJobRun(
                job=job,
                status=status,
                started_at=started,
                finished_at=finished,
                rows_affected=rows,
                detail=f"{job} run {i}",
            )
            session.add(run)

    session.commit()


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    test_app = build_app()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with next(override_get_session()) as session:
        _seed_data(session)

    client = test_app.__class__.__bases__[0]().test_client if hasattr(test_app, "test_client") else None
    if not client:
        from fastapi.testclient import TestClient
        client = TestClient(test_app)

    response = client.get("/api/cadence/sla-report")
    if response.status_code != 200:
        print(f"FAIL: status {response.status_code}")
        sys.exit(1)

    data = response.json()
    for job in data["jobs"]:
        sr = job["success_rate"]
        p95 = job["p95_duration"]
        if not (0 <= sr <= 100):
            print(f"FAIL: success_rate {sr} out of range for {job['name']}")
            sys.exit(1)
        if p95 <= 0:
            print(f"FAIL: p95_duration {p95} not > 0 for {job['name']}")
            sys.exit(1)

    print("PASS")