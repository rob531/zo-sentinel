# services/staged/sprint_progress_dashboard/contract.py

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

# Real data layer imports (must not be re‑implemented)
from app.db import Base, get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


class JobStats(BaseModel):
    name: str
    run_count: int
    pass_count: int
    fail_count: int
    avg_duration_sec: Optional[float]
    last_run_at: Optional[datetime]


class SprintProgressResponse(BaseModel):
    sprint_id: Optional[int] = None
    jobs: List[JobStats]


def _aggregate_runs(runs: List[CadenceJobRun]) -> List[JobStats]:
    """Aggregate a list of CadenceJobRun rows into JobStats."""
    agg = {}
    for run in runs:
        key = run.job
        if key not in agg:
            agg[key] = {
                "run_count": 0,
                "pass_count": 0,
                "fail_count": 0,
                "total_duration": 0.0,
                "last_run_at": None,
            }
        entry = agg[key]
        entry["run_count"] += 1
        if run.status == "pass":
            entry["pass_count"] += 1
        elif run.status == "fail":
            entry["fail_count"] += 1

        # duration in seconds (finished_at - started_at)
        if run.started_at and run.finished_at:
            duration = (run.finished_at - run.started_at).total_seconds()
            entry["total_duration"] += duration

        if not entry["last_run_at"] or (run.finished_at and run.finished_at > entry["last_run_at"]):
            entry["last_run_at"] = run.finished_at

    result: List[JobStats] = []
    for job_name, data in agg.items():
        avg = (
            data["total_duration"] / data["run_count"]
            if data["run_count"] > 0
            else None
        )
        result.append(
            JobStats(
                name=job_name,
                run_count=data["run_count"],
                pass_count=data["pass_count"],
                fail_count=data["fail_count"],
                avg_duration_sec=avg,
                last_run_at=data["last_run_at"],
            )
        )
    return result


def get_sprint_progress(db: Session) -> SprintProgressResponse:
    """Read CadenceJobRun rows and return aggregated sprint‑progress data."""
    runs = db.query(CadenceJobRun).order_by(CadenceJobRun.job).all()
    jobs = _aggregate_runs(runs)
    return SprintProgressResponse(sprint_id=None, jobs=jobs)


@router.get(
    "/dashboard/sprint-progress",
    response_model=SprintProgressResponse,
    name="sprint_progress_dashboard",
)
def sprint_progress_endpoint(db: Session = Depends(get_session)):
    """GET /api/dashboard/sprint-progress – returns sprint progress aggregation."""
    return get_sprint_progress(db)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.sprint_progress_dashboard.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    # ------------------------------------------------------------------- #
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine (StaticPool makes it behave like a singleton)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables using the real Base metadata
    Base.metadata.create_all(bind=engine)

    # Dependency override to use the test session
    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed deterministic data: 3 jobs, 4 runs each (2 pass, 2 fail)
    # ------------------------------------------------------------------- #
    with TestingSessionLocal() as db:
        now = datetime.utcnow()
        for job_name in ("jobA", "jobB", "jobC"):
            for i in range(4):
                status = "pass" if i < 2 else "fail"
                started = now - timedelta(minutes=i + 1)
                finished = started + timedelta(seconds=10)
                db.add(
                    CadenceJobRun(
                        job=job_name,
                        status=status,
                        started_at=started,
                        finished_at=finished,
                        detail="self‑test",
                        rows_affected=0,
                    )
                )
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute the request against the test client
    # ------------------------------------------------------------------- #
    client = TestClient(test_app)
    response = client.get("/api/dashboard/sprint-progress")
    try:
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()
        assert "jobs" in payload, "Missing 'jobs' key"
        assert len(payload["jobs"]) == 3, f"Expected 3 jobs, got {len(payload['jobs'])}"
        job_a = next(j for j in payload["jobs"] if j["name"] == "jobA")
        assert job_a["pass_count"] == 2, f"jobA pass_count expected 2, got {job_a['pass_count']}"
    except AssertionError as exc:
        print(f"SELF‑TEST FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)