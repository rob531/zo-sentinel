from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun
from .logic import get_cadence_job_duration_rollup

router = APIRouter(prefix="/api")


class JobDurationRollup(BaseModel):
    job: str
    runs: int
    ok: int
    failed: int
    p50_seconds: Optional[float]
    p90_seconds: Optional[float]
    max_seconds: Optional[float]
    rows_affected_total: int
    last_started_at: Optional[datetime]


class RollupTotals(BaseModel):
    runs: int
    failed: int
    rows_affected: int


class CadenceJobDurationRollupResponse(BaseModel):
    days: int
    jobs: List[JobDurationRollup]
    totals: RollupTotals


@router.get(
    "/cadence/job-duration-rollup",
    response_model=CadenceJobDurationRollupResponse,
)
def cadence_job_duration_rollup(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> CadenceJobDurationRollupResponse:
    return get_cadence_job_duration_rollup(session, days)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    rows_affected INTEGER,
                    detail TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(microsecond=0)
        durations = [10, 20, 30, 40, 50]
        rows = []
        for index, duration in enumerate(durations, start=1):
            job = "single-run-job" if index == 1 else "multi-run-job"
            started_at = now - timedelta(minutes=10)
            rows.append(
                {
                    "id": index,
                    "job": job,
                    "status": "failed" if index in (2, 5) else "success",
                    "started_at": started_at,
                    "finished_at": started_at + timedelta(seconds=duration),
                    "rows_affected": index,
                    "detail": "{}",
                }
            )

        for row in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO cadence_job_runs
                        (id, job, status, started_at, finished_at, rows_affected, detail)
                    VALUES
                        (:id, :job, :status, :started_at, :finished_at, :rows_affected, :detail)
                    """
                ),
                row,
            )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/cadence/job-duration-rollup")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["jobs"]) == 2, payload
            assert payload["totals"]["runs"] == 5, payload

            single = next(job for job in payload["jobs"] if job["job"] == "single-run-job")
            assert single["p50_seconds"] == 10, payload
            assert single["max_seconds"] == 10, payload

        assert CadenceJobRun.__tablename__ == "cadence_job_runs"
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()