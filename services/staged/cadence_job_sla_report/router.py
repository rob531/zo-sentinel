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
    import sqlite3
    from fastapi.testclient import TestClient
    from main import app
    from datetime import timedelta

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE cadence_job_runs (
            job TEXT, status TEXT, started_at TEXT, finished_at TEXT, rows_affected INTEGER
        )
    """)
    base = datetime(2024, 1, 1, 0, 0, 0)
    test_data = [
        ("data_sync", "success", base + timedelta(seconds=1), base + timedelta(seconds=121), 500),
        ("data_sync", "success", base + timedelta(seconds=200), base + timedelta(seconds=300), 600),
        ("data_sync", "success", base + timedelta(seconds=400), base + timedelta(seconds=500), 550),
        ("data_sync", "success", base + timedelta(seconds=600), base + timedelta(seconds=700), 480),
        ("data_sync", "success", base + timedelta(seconds=800), base + timedelta(seconds=900), 520),
        ("etl_batch", "success", base + timedelta(seconds=1000), base + timedelta(seconds=1600), 10000),
        ("etl_batch", "failed", base + timedelta(seconds=1700), base + timedelta(seconds=2400), 0),
        ("etl_batch", "success", base + timedelta(seconds=2500), base + timedelta(seconds=3200), 12000),
        ("etl_batch", "failed", base + timedelta(seconds=3300), base + timedelta(seconds=4100), 0),
        ("etl_batch", "success", base + timedelta(seconds=4200), base + timedelta(seconds=5000), 11000),
        ("report_gen", "success", base + timedelta(seconds=10), base + timedelta(seconds=80), 200),
        ("report_gen", "failed", base + timedelta(seconds=200), base + timedelta(seconds=350), 0),
        ("report_gen", "success", base + timedelta(seconds=400), base + timedelta(seconds=510), 180),
        ("report_gen", "success", base + timedelta(seconds=600), base + timedelta(seconds=780), 220),
        ("report_gen", "success", base + timedelta(seconds=900), base + timedelta(seconds=1100), 190),
    ]
    for row in test_data:
        cursor.execute("INSERT INTO cadence_job_runs VALUES (?, ?, ?, ?, ?)", row)
    conn.commit()

    def get_test_session():
        return sqlite3.connect(":memory:")

    from app.db import get_session as real_get_session
    app.dependency_overrides[real_get_session] = lambda: sqlite3.connect(":memory:")
    test_conn = sqlite3.connect(":memory:")
    test_conn.execute("""
        CREATE TABLE cadence_job_runs (
            job TEXT, status TEXT, started_at TEXT, finished_at TEXT, rows_affected INTEGER
        )
    """)
    for row in test_data:
        test_conn.execute("INSERT INTO cadence_job_runs VALUES (?, ?, ?, ?, ?)", row)
    test_conn.commit()

    def get_test_session():
        test_conn.row_factory = sqlite3.Row
        return test_conn

    app.dependency_overrides[real_get_session] = get_test_session
    client = TestClient(app)
    response = client.get("/api/cadence/sla-report")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    jobs = data.get("jobs", [])
    assert len(jobs) >= 3, f"Expected at least 3 jobs, got {len(jobs)}"
    has_success = any(job["success_rate"] > 0 for job in jobs)
    assert has_success, "Expected at least one job with success_rate > 0"
    print(f"Jobs found: {len(jobs)}")
    for job in jobs:
        print(f"  - {job['name']}: success_rate={job['success_rate']:.2%}")
    print("PASS")