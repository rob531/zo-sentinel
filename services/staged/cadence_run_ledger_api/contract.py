from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


RUNS_SQL = """
SELECT id, job, status, started_at, finished_at, rows_affected
FROM cadence_job_runs
ORDER BY started_at DESC
"""

RUNS_BY_JOB_SQL = """
SELECT id, job, status, started_at, finished_at, rows_affected
FROM cadence_job_runs
WHERE job = :job
ORDER BY started_at DESC
"""


class CadenceRun(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    duration_seconds: Optional[float]
    rows_affected: int


class CadenceJobSummary(BaseModel):
    job: str
    runs: int
    failures: int
    avg_duration_seconds: Optional[float]


class CadenceRunsResponse(BaseModel):
    runs: List[CadenceRun]
    total: int
    jobs: List[CadenceJobSummary]
    failures: int


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _duration_seconds(
    started_at: Optional[datetime], finished_at: Optional[datetime]
) -> Optional[float]:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return None
    return float((finished - started).total_seconds())


def get_cadence_runs(
    session: Session, job: Optional[str] = None, limit: int = 50
) -> Dict[str, Any]:
    limit = max(1, min(500, int(limit)))
    if job is None:
        result = session.execute(text(RUNS_SQL))
    else:
        result = session.execute(text(RUNS_BY_JOB_SQL), {"job": job})

    rows = result.mappings().all()
    summaries: Dict[str, Dict[str, Any]] = {}
    failures = 0

    for row in rows:
        job_name = str(row["job"])
        status = str(row["status"])
        duration = _duration_seconds(row["started_at"], row["finished_at"])

        summary = summaries.setdefault(
            job_name,
            {
                "job": job_name,
                "runs": 0,
                "failures": 0,
                "_durations": [],
            },
        )
        summary["runs"] += 1
        if status.lower() == "failed":
            summary["failures"] += 1
            failures += 1
        if duration is not None:
            summary["_durations"].append(duration)

    jobs = []
    for job_name in sorted(summaries):
        summary = summaries[job_name]
        durations = summary.pop("_durations")
        summary["avg_duration_seconds"] = (
            float(sum(durations) / len(durations)) if durations else None
        )
        jobs.append(summary)

    runs = []
    for row in rows[:limit]:
        started_at = _as_datetime(row["started_at"])
        finished_at = _as_datetime(row["finished_at"])
        runs.append(
            {
                "id": int(row["id"]),
                "job": str(row["job"]),
                "status": str(row["status"]),
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": _duration_seconds(started_at, finished_at),
                "rows_affected": int(row["rows_affected"]),
            }
        )

    return {
        "runs": runs,
        "total": len(rows),
        "jobs": jobs,
        "failures": failures,
    }


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    CadenceJobRun.__table__.create(engine)
    session = TestingSessionLocal()
    try:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="daily-index",
                    status="completed",
                    started_at=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
                    finished_at=datetime(2024, 1, 1, 10, 0, 12, tzinfo=timezone.utc),
                    rows_affected=10,
                    detail="seed row",
                ),
                CadenceJobRun(
                    id=2,
                    job="daily-index",
                    status="failed",
                    started_at=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
                    finished_at=datetime(2024, 1, 2, 10, 0, 4, tzinfo=timezone.utc),
                    rows_affected=0,
                    detail="seed row",
                ),
                CadenceJobRun(
                    id=3,
                    job="weekly-scan",
                    status="running",
                    started_at=datetime(2024, 1, 3, 10, 0, tzinfo=timezone.utc),
                    finished_at=None,
                    rows_affected=0,
                    detail="seed row",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/runs", response_model=CadenceRunsResponse)
    def cadence_runs(
        job: Optional[str] = None,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return get_cadence_runs(db, job=job, limit=limit)

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/runs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["failures"] == 1
        assert len(payload["jobs"]) == 2
        assert len(payload["runs"]) == 3

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()