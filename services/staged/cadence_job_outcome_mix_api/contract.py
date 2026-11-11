import datetime
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


class JobOutcomeMix(BaseModel):
    job: str
    total: int
    by_status: Dict[str, int]
    failed_share: float
    avg_rows_affected: float


class Totals(BaseModel):
    runs: int
    jobs: int
    failed: int
    failed_share: float


class OutcomeMixResponse(BaseModel):
    jobs: List[JobOutcomeMix]
    totals: Totals


@router.get("/cadence/jobs/outcome-mix", response_model=OutcomeMixResponse)
def get_outcome_mix(session: Session = Depends(get_session)):
    rows = session.execute(select(CadenceJobRun)).scalars().all()

    job_stats: Dict[str, Dict] = {}
    overall_failed = 0

    for r in rows:
        job = r.job
        status = r.status
        if job not in job_stats:
            job_stats[job] = {
                "total": 0,
                "by_status": defaultdict(int),
                "rows_affected_sum": 0,
                "rows_affected_count": 0,
                "failed": 0,
            }
        stats = job_stats[job]
        stats["total"] += 1
        stats["by_status"][status] += 1
        stats["rows_affected_sum"] += r.rows_affected or 0
        stats["rows_affected_count"] += 1
        if status == "failed":
            stats["failed"] += 1
            overall_failed += 1

    jobs_out: List[JobOutcomeMix] = []
    for job, stats in job_stats.items():
        total = stats["total"]
        failed = stats["failed"]
        avg_rows = (
            stats["rows_affected_sum"] / stats["rows_affected_count"]
            if stats["rows_affected_count"]
            else 0.0
        )
        jobs_out.append(
            JobOutcomeMix(
                job=job,
                total=total,
                by_status=dict(stats["by_status"]),
                failed_share=failed / total if total else 0.0,
                avg_rows_affected=avg_rows,
            )
        )

    total_runs = len(rows)
    total_jobs = len(job_stats)
    overall_failed_share = overall_failed / total_runs if total_runs else 0.0

    return OutcomeMixResponse(
        jobs=jobs_out,
        totals=Totals(
            runs=total_runs,
            jobs=total_jobs,
            failed=overall_failed,
            failed_share=overall_failed_share,
        ),
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # In‑memory SQLite setup
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:
        with SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = datetime.datetime.utcnow()
    seed = [
        CadenceJobRun(
            job="jobA",
            status="success",
            started_at=now,
            finished_at=now,
            rows_affected=10,
            detail="",
        ),
        CadenceJobRun(
            job="jobA",
            status="success",
            started_at=now,
            finished_at=now,
            rows_affected=20,
            detail="",
        ),
        CadenceJobRun(
            job="jobA",
            status="failed",
            started_at=now,
            finished_at=now,
            rows_affected=5,
            detail="",
        ),
        CadenceJobRun(
            job="jobB",
            status="success",
            started_at=now,
            finished_at=now,
            rows_affected=15,
            detail="",
        ),
        CadenceJobRun(
            job="jobC",
            status="failed",
            started_at=now,
            finished_at=now,
            rows_affected=8,
            detail="",
        ),
    ]
    with SessionLocal() as s:
        s.add_all(seed)
        s.commit()

    client = TestClient(app)
    resp = client.get("/api/cadence/jobs/outcome-mix")
    assert resp.status_code == 200
    data = resp.json()
    job_names = {j["job"] for j in data["jobs"]}
    assert "jobA" in job_names
    for j in data["jobs"]:
        if j["job"] == "jobA":
            assert j["total"] == 3
            assert j["by_status"]["success"] == 2
            assert j["by_status"]["failed"] == 1
    print("PASS")