"""logic.py -- the service's data/computation layer (one concern, one file)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _bounded_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 50
    return max(1, min(200, value))


def _run_dict(run: CadenceJobRun) -> Dict[str, Any]:
    return {
        "id": run.id,
        "job": run.job,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "rows_affected": run.rows_affected,
        "detail": run.detail,
    }


def list_cadence_runs(
    db: Session = Depends(get_session),
    job: str | None = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return recent cadence runs and a summary for each matching job."""
    bounded_limit = _bounded_limit(limit)
    statement = select(CadenceJobRun)
    if job:
        statement = statement.where(CadenceJobRun.job == job)

    recent_runs = db.execute(
        statement.order_by(
            CadenceJobRun.started_at.desc(),
            CadenceJobRun.id.desc(),
        ).limit(bounded_limit)
    ).scalars().all()

    summary_statement = select(
        CadenceJobRun.job,
        func.count(CadenceJobRun.id),
        func.sum(
            case(
                (CadenceJobRun.status == "failed", 1),
                else_=0,
            )
        ),
    ).group_by(CadenceJobRun.job)
    if job:
        summary_statement = summary_statement.where(CadenceJobRun.job == job)

    summary_rows = db.execute(summary_statement.order_by(CadenceJobRun.job)).all()

    jobs = []
    for job_name, run_count, failure_count in summary_rows:
        latest = db.execute(
            select(CadenceJobRun)
            .where(CadenceJobRun.job == job_name)
            .order_by(
                CadenceJobRun.started_at.desc(),
                CadenceJobRun.id.desc(),
            )
            .limit(1)
        ).scalars().first()

        jobs.append(
            {
                "job": job_name,
                "last_status": latest.status if latest is not None else None,
                "last_started_at": latest.started_at if latest is not None else None,
                "last_finished_at": latest.finished_at if latest is not None else None,
                "last_rows_affected": (
                    latest.rows_affected if latest is not None else None
                ),
                "run_count": int(run_count or 0),
                "failure_count": int(failure_count or 0),
            }
        )

    return {
        "jobs": jobs,
        "runs": [_run_dict(run) for run in recent_runs],
    }


get_cadence_run_history = list_cadence_runs
get_cadence_runs = list_cadence_runs
get_run_history = list_cadence_runs
cadence_run_history = list_cadence_runs


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    CadenceJobRun.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    session = TestingSession()
    now = datetime.utcnow()
    session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="alpha",
                status="success",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                rows_affected=3,
                detail=None,
            ),
            CadenceJobRun(
                id=2,
                job="beta",
                status="failed",
                started_at=now - timedelta(minutes=4),
                finished_at=now - timedelta(minutes=3),
                rows_affected=0,
                detail="first failure",
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="failed",
                started_at=now - timedelta(minutes=3),
                finished_at=now - timedelta(minutes=2),
                rows_affected=0,
                detail="second failure",
            ),
            CadenceJobRun(
                id=4,
                job="gamma",
                status="success",
                started_at=now - timedelta(minutes=2),
                finished_at=now - timedelta(minutes=1),
                rows_affected=7,
                detail=None,
            ),
            CadenceJobRun(
                id=5,
                job="alpha",
                status="success",
                started_at=now - timedelta(minutes=1),
                finished_at=now,
                rows_affected=2,
                detail=None,
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield session

    test_app.dependency_overrides[get_session] = override_get_session

    result = list_cadence_runs(db=session)
    assert len(result["jobs"]) == 3
    assert next(item for item in result["jobs"] if item["job"] == "beta")[
        "failure_count"
    ] == 2
    limited = list_cadence_runs(db=session, limit=1)
    assert len(limited["runs"]) == 1
    print("PASS")