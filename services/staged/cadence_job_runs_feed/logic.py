"""logic.py -- cadence job runs feed data/computation layer.

Reads the real cadence_job_runs application table through the application's
SQLAlchemy session and model. The module contains no database or model stubs.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _duration_seconds(
    started_at: Any, finished_at: Any
) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None
    try:
        return round((finished_at - started_at).total_seconds(), 3)
    except (AttributeError, TypeError, ValueError):
        return None


def get_cadence_job_runs(
    db: Session,
    job: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return cadence runs, optionally restricted to one job.

    Without a job filter, only the most recent run for each distinct job is
    returned. With a job filter, all matching runs are returned, ordered from
    newest to oldest.
    """
    try:
        normalized_limit = max(0, int(limit))
    except (TypeError, ValueError):
        normalized_limit = 50

    if job is None:
        ranked = (
            select(
                CadenceJobRun,
                func.row_number()
                .over(
                    partition_by=CadenceJobRun.job,
                    order_by=(
                        CadenceJobRun.started_at.desc(),
                        CadenceJobRun.id.desc(),
                    ),
                )
                .label("job_rank"),
            )
            .subquery()
        )
        statement = (
            select(CadenceJobRun)
            .join(ranked, CadenceJobRun.id == ranked.c.id)
            .where(ranked.c.job_rank == 1)
            .order_by(
                CadenceJobRun.started_at.desc(),
                CadenceJobRun.id.desc(),
            )
        )
    else:
        statement = (
            select(CadenceJobRun)
            .where(CadenceJobRun.job == job)
            .order_by(
                CadenceJobRun.started_at.desc(),
                CadenceJobRun.id.desc(),
            )
        )

    if normalized_limit == 0:
        rows = []
    else:
        rows = list(db.execute(statement.limit(normalized_limit)).scalars().all())

    runs = []
    for run in rows:
        runs.append(
            {
                "id": run.id,
                "job": run.job,
                "status": run.status,
                "started_at": _timestamp(run.started_at),
                "finished_at": _timestamp(run.finished_at),
                "rows_affected": run.rows_affected,
                "detail": run.detail,
                "duration_seconds": _duration_seconds(
                    run.started_at, run.finished_at
                ),
            }
        )

    return {
        "job": job,
        "runs": runs,
        "count": len(runs),
    }


def list_cadence_job_runs(
    db: Session,
    job: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Compatibility alias for :func:`get_cadence_job_runs`."""
    return get_cadence_job_runs(db, job=job, limit=limit)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/cadence/runs")
    def cadence_runs(
        job: Optional[str] = Query(default=None),
        limit: int = Query(default=50),
        db: Session = Depends(get_session),
    ):
        return get_cadence_job_runs(db, job=job, limit=limit)

    with TestingSessionLocal() as session:
        base = datetime(2024, 1, 1, 12, 0, 0)
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="completed",
                    started_at=base,
                    finished_at=base + timedelta(seconds=2),
                    rows_affected=10,
                    detail={"message": "first alpha run"},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="completed",
                    started_at=base + timedelta(hours=1),
                    finished_at=base + timedelta(hours=1, seconds=4),
                    rows_affected=20,
                    detail={"message": "second alpha run"},
                ),
                CadenceJobRun(
                    job="beta",
                    status="failed",
                    started_at=base + timedelta(minutes=10),
                    finished_at=base + timedelta(minutes=10, seconds=3),
                    rows_affected=5,
                    detail={"message": "first beta run"},
                ),
                CadenceJobRun(
                    job="beta",
                    status="completed",
                    started_at=base + timedelta(hours=2),
                    finished_at=base + timedelta(hours=2, seconds=1),
                    rows_affected=8,
                    detail={"message": "second beta run"},
                ),
            ]
        )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/runs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert len(payload["runs"]) == 2
        assert {run["job"] for run in payload["runs"]} == {"alpha", "beta"}

        response = client.get("/api/cadence/runs", params={"job": "alpha"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["job"] == "alpha"
        assert payload["count"] == 2
        assert all(run["job"] == "alpha" for run in payload["runs"])
        assert all(run["duration_seconds"] is not None for run in payload["runs"])
        assert {run["duration_seconds"] for run in payload["runs"]} == {2.0, 4.0}

    print("PASS")