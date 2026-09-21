from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class JobMetrics(BaseModel):
    name: str
    total_runs: int
    success_rate: float
    avg_duration_sec: float
    p95_duration_sec: float
    sla_compliance_pct: float
    rows_throughput: float


class SLAReportResponse(BaseModel):
    jobs: List[JobMetrics]


def get_sla_target_seconds(job_type: str) -> float:
    sla_targets = {"data_sync": 120.0, "etl_batch": 900.0, "report_gen": 300.0}
    return sla_targets.get(job_type, 300.0)


@router.get("/sla-report", response_model=SLAReportResponse)
def get_sla_report(db: Session = Depends(get_session)) -> SLAReportResponse:
    query = text("""
        SELECT job, status, started_at, finished_at, rows_affected
        FROM cadence_job_runs
        WHERE finished_at IS NOT NULL AND started_at IS NOT NULL
        ORDER BY job
    """)
    result = db.execute(query)
    rows = result.fetchall()

    job_runs = {}
    for row in rows:
        job_name = row[0]
        if job_name not in job_runs:
            job_runs[job_name] = []
        started = row[2] if isinstance(row[2], datetime) else datetime.fromisoformat(str(row[2]))
        finished = row[3] if isinstance(row[3], datetime) else datetime.fromisoformat(str(row[3]))
        job_runs[job_name].append({
            "status": row[1],
            "duration_sec": (finished - started).total_seconds(),
            "rows_affected": row[4] or 0
        })

    jobs = []
    for job_name, runs in job_runs.items():
        total_runs = len(runs)
        success_count = sum(1 for r in runs if r["status"] == "success")
        success_rate = success_count / total_runs if total_runs > 0 else 0.0
        durations = [r["duration_sec"] for r in runs]
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        p95_duration = avg_duration
        if len(durations) > 1:
            sorted_durations = sorted(durations)
            idx = 0.95 * (len(sorted_durations) - 1)
            lower = int(idx)
            upper = lower + 1
            weight = idx - lower
            p95_duration = sorted_durations[lower] * (1 - weight) + sorted_durations[upper] * weight
        sla_target = get_sla_target_seconds(job_name)
        sla_compliant = sum(1 for d in durations if d <= sla_target)
        sla_compliance = sla_compliant / total_runs if total_runs > 0 else 0.0
        rows_list = [r["rows_affected"] for r in runs]
        rows_throughput = sum(rows_list) / len(rows_list) if rows_list else 0.0
        jobs.append(JobMetrics(
            name=job_name,
            total_runs=total_runs,
            success_rate=round(success_rate, 4),
            avg_duration_sec=round(avg_duration, 2),
            p95_duration_sec=round(p95_duration, 2),
            sla_compliance_pct=round(sla_compliance, 4),
            rows_throughput=round(rows_throughput, 2)
        ))

    return SLAReportResponse(jobs=jobs)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session as real_get_session
    from app.models import CadenceJobRun  # registers the table on Base.metadata

    # MERGE_AUDIT_2026-08-23 L3. This self-test used to open with
    # `from main import app` -- a bare top-level `main` that resolves to
    # nothing. It is inert at mount time (it sits inside __main__), so the
    # service imports and mounts correctly and no gate ever walked this path.
    # It matters because this file is the FIRST autonomously promoted service
    # (#3171): it is the exemplar the promotion lane copies, so the defect
    # propagating matters more than the defect.
    #
    # Rebuilt the way the builder recipe prescribes and this service's own
    # contract.py already does it: a LOCAL FastAPI() plus a real in-memory
    # SQLAlchemy session. Importing the global app.main:app would drag the whole
    # generated spine into a unit self-test. The previous body also overrode
    # get_session with a raw sqlite3 connection, which cannot execute the
    # TextClause this router passes -- so the self-test could not have passed
    # even with the import repaired.
    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    base_ts = datetime(2024, 1, 1, 0, 0, 0)
    # (job, status, start_offset_s, end_offset_s, rows_affected)
    seed = [
        ("data_sync", "success", 1, 121, 500),
        ("data_sync", "success", 200, 300, 600),
        ("data_sync", "success", 400, 500, 550),
        ("data_sync", "success", 600, 700, 480),
        ("data_sync", "success", 800, 900, 520),
        ("etl_batch", "success", 1000, 1600, 10000),
        ("etl_batch", "failed", 1700, 2400, 0),
        ("etl_batch", "success", 2500, 3200, 12000),
        ("etl_batch", "failed", 3300, 4100, 0),
        ("etl_batch", "success", 4200, 5000, 11000),
        ("report_gen", "success", 10, 80, 200),
        ("report_gen", "failed", 200, 350, 0),
        ("report_gen", "success", 400, 510, 180),
        ("report_gen", "success", 600, 780, 220),
        ("report_gen", "success", 900, 1100, 190),
    ]
    with TestSessionLocal() as db:
        for job, status, start_s, end_s, rows_affected in seed:
            db.add(CadenceJobRun(
                job=job,
                status=status,
                started_at=base_ts + timedelta(seconds=start_s),
                finished_at=base_ts + timedelta(seconds=end_s),
                rows_affected=rows_affected,
            ))
        db.commit()

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[real_get_session] = get_test_session

    client = TestClient(app)
    response = client.get("/api/cadence/sla-report")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    jobs = {j["name"]: j for j in response.json().get("jobs", [])}
    assert len(jobs) == 3, f"Expected 3 jobs, got {len(jobs)}"
    assert jobs["data_sync"]["total_runs"] == 5
    assert jobs["data_sync"]["success_rate"] == 1.0, "data_sync is all-success"
    assert jobs["etl_batch"]["success_rate"] == 0.6, "etl_batch is 3/5 success"
    assert jobs["report_gen"]["success_rate"] == 0.8, "report_gen is 4/5 success"
    # data_sync's SLA target is 120s and every run is <=120s.
    assert jobs["data_sync"]["sla_compliance_pct"] == 1.0
    assert jobs["etl_batch"]["rows_throughput"] > 0

    for name, job in sorted(jobs.items()):
        print(f"  - {name}: success_rate={job['success_rate']:.2%} "
              f"sla={job['sla_compliance_pct']:.2%}")
    print("PASS")
