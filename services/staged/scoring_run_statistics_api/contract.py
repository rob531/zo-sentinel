# services/staged/scoring_run_statistics_api/contract.py
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api/scoring/runs", tags=["scoring_run_statistics"])


class JobStats(BaseModel):
    job: str
    total_runs: int
    pass_count: int
    fail_count: int
    error_count: int
    avg_duration_seconds: Optional[float]
    last_run_started: Optional[datetime]
    last_run_finished: Optional[datetime]
    last_run_status: Optional[str]
    rows_affected_last: Optional[int]


class ScoringRunStatisticsResponse(BaseModel):
    summary: Dict[str, int]
    per_job_stats: List[JobStats]


@router.get("/statistics", response_model=ScoringRunStatisticsResponse)
def get_scoring_run_statistics(session: Session = Depends(get_session)):
    # fetch all runs ordered by most recent start
    runs = (
        session.query(CadenceJobRun)
        .order_by(CadenceJobRun.started_at.desc())
        .all()
    )

    # aggregate per job
    agg: Dict[str, Dict] = {}
    for run in runs:
        job = run.job
        if job not in agg:
            agg[job] = {
                "total_runs": 0,
                "pass_count": 0,
                "fail_count": 0,
                "error_count": 0,
                "duration_sum": 0.0,
                "duration_cnt": 0,
                "last_run_started": None,
                "last_run_finished": None,
                "last_run_status": None,
                "rows_affected_last": None,
            }

        stats = agg[job]
        stats["total_runs"] += 1

        if run.status == "PASS":
            stats["pass_count"] += 1
        elif run.status == "FAIL":
            stats["fail_count"] += 1
        else:
            stats["error_count"] += 1

        # duration calculation if finished
        if run.finished_at and run.started_at:
            duration = (run.finished_at - run.started_at).total_seconds()
            stats["duration_sum"] += duration
            stats["duration_cnt"] += 1

        # because runs are ordered newest first, the first occurrence is the latest
        if stats["last_run_started"] is None:
            stats["last_run_started"] = run.started_at
            stats["last_run_finished"] = run.finished_at
            stats["last_run_status"] = run.status
            stats["rows_affected_last"] = run.rows_affected

    per_job_stats: List[JobStats] = []
    summary_counts = {
        "total_runs": 0,
        "pass_count": 0,
        "fail_count": 0,
        "error_count": 0,
    }

    for job, s in agg.items():
        avg_duration = (
            s["duration_sum"] / s["duration_cnt"]
            if s["duration_cnt"] > 0
            else None
        )
        per_job_stats.append(
            JobStats(
                job=job,
                total_runs=s["total_runs"],
                pass_count=s["pass_count"],
                fail_count=s["fail_count"],
                error_count=s["error_count"],
                avg_duration_seconds=avg_duration,
                last_run_started=s["last_run_started"],
                last_run_finished=s["last_run_finished"],
                last_run_status=s["last_run_status"],
                rows_affected_last=s["rows_affected_last"],
            )
        )
        # accumulate summary
        summary_counts["total_runs"] += s["total_runs"]
        summary_counts["pass_count"] += s["pass_count"]
        summary_counts["fail_count"] += s["fail_count"]
        summary_counts["error_count"] += s["error_count"]

    return ScoringRunStatisticsResponse(
        summary=summary_counts,
        per_job_stats=per_job_stats,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.scoring_run_statistics_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build a temporary in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables using the real model metadata
    from app.models import Base  # noqa: E402

    Base.metadata.create_all(bind=engine)

    # Populate with test data
    test_session = SessionLocal()
    now = datetime.utcnow()
    rows = [
        CadenceJobRun(
            job="job1",
            status="PASS",
            started_at=now,
            finished_at=now,
            rows_affected=10,
            detail="{}",
        ),
        CadenceJobRun(
            job="job1",
            status="PASS",
            started_at=now,
            finished_at=now,
            rows_affected=12,
            detail="{}",
        ),
        CadenceJobRun(
            job="job1",
            status="FAIL",
            started_at=now,
            finished_at=now,
            rows_affected=5,
            detail="{}",
        ),
        CadenceJobRun(
            job="job1",
            status="RUNNING",
            started_at=now,
            finished_at=None,
            rows_affected=None,
            detail="{}",
        ),
    ]
    test_session.add_all(rows)
    test_session.commit()
    test_session.close()

    # FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/scoring/runs/statistics")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    summary = data.get("summary", {})
    if (
        summary.get("total_runs") != 4
        or summary.get("pass_count") != 2
        or summary.get("fail_count") != 1
    ):
        print("FAIL: summary counts mismatch")
        sys.exit(1)

    print("PASS")
    sys.exit(0)