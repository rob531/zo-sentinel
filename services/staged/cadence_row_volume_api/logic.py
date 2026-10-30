from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None

    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    if isinstance(finished_at, str):
        finished_at = datetime.fromisoformat(finished_at)

    try:
        return (finished_at - started_at).total_seconds()
    except (TypeError, AttributeError):
        return None


def get_cadence_row_volume(
    db: Session = Depends(get_session),
    job: Optional[str] = None,
) -> Dict[str, Any]:
    """Return run and row-volume statistics for cadence jobs.

    The aggregation is performed from the application's authoritative
    ``cadence_job_runs`` model.  The selected columns are portable across
    PostgreSQL and SQLite, while duration arithmetic is performed in Python
    to avoid database-specific timestamp functions.
    """
    statement = select(
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
        CadenceJobRun.rows_affected,
    )
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    rows = db.execute(statement).all()
    grouped: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "run_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_rows_affected": 0,
            "_rows_sum": 0,
            "_duration_sum": 0.0,
            "_duration_count": 0,
        }
    )

    for row in rows:
        job_name = row.job
        if job_name is None:
            continue

        stats = grouped[job_name]
        stats["run_count"] += 1

        status = row.status
        if status == "success":
            stats["success_count"] += 1
        else:
            stats["failure_count"] += 1

        rows_affected = row.rows_affected or 0
        stats["total_rows_affected"] += rows_affected
        stats["_rows_sum"] += rows_affected

        duration = _duration_seconds(row.started_at, row.finished_at)
        if duration is not None:
            stats["_duration_sum"] += duration
            stats["_duration_count"] += 1

    jobs: List[Dict[str, Any]] = []
    for job_name in sorted(grouped):
        stats = grouped[job_name]
        run_count = stats["run_count"]
        duration_count = stats["_duration_count"]

        jobs.append(
            {
                "job": job_name,
                "run_count": run_count,
                "success_count": stats["success_count"],
                "failure_count": stats["failure_count"],
                "total_rows_affected": stats["total_rows_affected"],
                "avg_rows_affected": (
                    stats["_rows_sum"] / run_count if run_count else 0.0
                ),
                "avg_duration_seconds": (
                    stats["_duration_sum"] / duration_count
                    if duration_count
                    else 0.0
                ),
            }
        )

    return {"jobs": jobs, "total_jobs": len(jobs)}


# Kept as a short alias for callers that use the service name directly.
get_row_volume = get_cadence_row_volume


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    rows_affected INTEGER,
                    detail VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'scan', 'success', '2024-01-01 00:00:00',
                     '2024-01-01 00:00:10', 10, 'first scan'),
                    (2, 'scan', 'success', '2024-01-01 00:01:00',
                     '2024-01-01 00:01:20', 20, 'second scan'),
                    (3, 'score', 'failed', '2024-01-01 00:02:00',
                     '2024-01-01 00:02:05', 5, 'scoring failed')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def local_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = local_get_session

    @that_app.get("/api/cadence/row-volume")
    def row_volume_route(
        job: Optional[str] = None,
        db: Session = Depends(get_session),
    ):
        return get_cadence_row_volume(db=db, job=job)

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/row-volume")
        assert response.status_code == 200, response.text
        payload = response.json()

    assert payload["total_jobs"] == 2, payload
    entries = {entry["job"]: entry for entry in payload["jobs"]}

    assert entries["scan"]["run_count"] == 2, payload
    assert entries["scan"]["total_rows_affected"] == 30, payload
    assert entries["scan"]["success_count"] == 2, payload
    assert entries["score"]["failure_count"] == 1, payload

    print("PASS")