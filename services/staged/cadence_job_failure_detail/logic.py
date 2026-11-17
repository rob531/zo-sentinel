"""Logic layer for the cadence job failure detail service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _utc_now_naive() -> datetime:
    """Return a portable UTC timestamp for DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_failures(
    db: Session = Depends(get_session),
    days: int = 7,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return recent non-successful job runs and per-job aggregates."""
    days = max(0, int(days))
    limit = max(1, min(int(limit), 500))
    cutoff = _utc_now_naive() - timedelta(days=days)

    runs = db.execute(
        select(CadenceJobRun)
        .where(
            CadenceJobRun.status != "success",
            CadenceJobRun.started_at >= cutoff,
        )
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .limit(limit)
    ).scalars().all()

    failures: List[Dict[str, Any]] = []
    by_job: Dict[str, Dict[str, Any]] = {}

    for run in runs:
        failures.append(
            {
                "id": run.id,
                "job": run.job,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "rows_affected": run.rows_affected,
                "detail": run.detail,
            }
        )

        aggregate = by_job.get(run.job)
        if aggregate is None:
            by_job[run.job] = {
                "job": run.job,
                "failures": 1,
                "last_failed_at": run.started_at,
                "last_status": run.status,
                "total_rows_affected": int(run.rows_affected or 0),
            }
        else:
            aggregate["failures"] += 1
            aggregate["total_rows_affected"] += int(run.rows_affected or 0)

    return {
        "days": days,
        "limit": limit,
        "failures": failures,
        "by_job": list(by_job.values()),
    }


get_failure_details = get_failures
get_cadence_job_failures = get_failures


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    CadenceJobRun.__table__.create(engine)

    now = _utc_now_naive()
    with TestSession() as db:
        db.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="alpha",
                    status="error",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(minutes=59),
                    rows_affected=3,
                    detail="boom",
                ),
                CadenceJobRun(
                    id=2,
                    job="beta",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1, minutes=59),
                    rows_affected=5,
                    detail="boom",
                ),
                CadenceJobRun(
                    id=3,
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    id=4,
                    job="beta",
                    status="success",
                    started_at=now - timedelta(days=10),
                    finished_at=now - timedelta(days=10, minutes=1),
                    rows_affected=10,
                    detail=None,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app = FastAPI()

    @test_app.get("/api/cadence/jobs/failures")
    def cadence_failures(
        days: int = 7,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return get_failures(db=db, days=days, limit=limit)

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/cadence/jobs/failures",
        params={"days": 7, "limit": 50},
    )
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["failures"]) == 2
    assert any(item["failures"] == 1 for item in payload["by_job"])
    assert all(
        item["started_at"] >= (now - timedelta(days=7)).isoformat()
        for item in payload["failures"]
    )
    print("PASS")