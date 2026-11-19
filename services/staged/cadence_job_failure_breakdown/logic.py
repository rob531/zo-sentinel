"""logic.py -- cadence job failure aggregation over the real application database."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"success", "ok", "succeeded"}


def get_failure_breakdown(db: Session, days: int = 30) -> List[Dict[str, object]]:
    """Return per-job cadence run statistics for the requested time window."""
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    ).scalars().all()

    by_job: Dict[str, Dict[str, object]] = {}
    for row in rows:
        job = row.job or "unknown"
        item = by_job.setdefault(
            job,
            {
                "job": job,
                "total": 0,
                "failed": 0,
                "failure_rate": 0.0,
                "last_failure_at": None,
                "rows_affected": 0,
            },
        )
        item["total"] = int(item["total"]) + 1
        item["rows_affected"] = int(item["rows_affected"]) + int(row.rows_affected or 0)

        if row.status not in _SUCCESS_STATUSES:
            item["failed"] = int(item["failed"]) + 1
            failure_at = row.finished_at or row.started_at
            previous = item["last_failure_at"]
            if failure_at is not None and (previous is None or failure_at > previous):
                item["last_failure_at"] = failure_at

    result = []
    for item in by_job.values():
        total = int(item["total"])
        failed = int(item["failed"])
        item["failure_rate"] = round(failed / total, 3) if total else 0.0
        result.append(item)

    result.sort(key=lambda item: (-int(item["failed"]), str(item["job"])))
    return result


# Names used by generated routers and callers.
get_cadence_job_failure_breakdown = get_failure_breakdown
get_cadence_job_failures = get_failure_breakdown
cadence_job_failure_breakdown = get_failure_breakdown


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestingSession()
    now = datetime.utcnow()

    session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="example-job",
                status="failed",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
                rows_affected=10,
                detail={"error": "boom"},
            ),
            CadenceJobRun(
                id=2,
                job="example-job",
                status="success",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2),
                rows_affected=20,
                detail={},
            ),
            CadenceJobRun(
                id=3,
                job="example-job",
                status="error",
                started_at=now - timedelta(hours=4),
                finished_at=now - timedelta(hours=3),
                rows_affected=5,
                detail={"error": "again"},
            ),
            CadenceJobRun(
                id=4,
                job="success-only-job",
                status="succeeded",
                started_at=now - timedelta(hours=1),
                finished_at=now,
                rows_affected=7,
                detail={},
            ),
        ]
    )
    session.commit()

    application = FastAPI()

    def override_get_session():
        yield session

    application.dependency_overrides[get_session] = override_get_session

    @application.get("/api/cadence/jobs/failures")
    def failures(days: int = 30, db: Session = Depends(get_session)):
        return {"days": days, "jobs": get_failure_breakdown(db, days)}

    response = TestClient(application).get(
        "/api/cadence/jobs/failures", params={"days": 30}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    jobs = {item["job"]: item for item in payload["jobs"]}
    assert jobs["example-job"]["failed"] == 2
    assert abs(jobs["example-job"]["failure_rate"] - 0.667) < 0.001
    assert jobs["success-only-job"]["failed"] == 0
    print("PASS")