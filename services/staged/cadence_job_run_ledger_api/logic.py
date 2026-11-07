"""Logic for the cadence job run ledger API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _as_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def get_cadence_runs(db: Session, limit: int = 100) -> Dict[str, Any]:
    """Return recent cadence runs and aggregate run statistics."""
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 100
    requested_limit = max(1, min(requested_limit, 500))

    recent_rows = db.execute(
        select(CadenceJobRun)
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .limit(requested_limit)
    ).scalars().all()

    runs = [
        {
            "id": int(row.id),
            "job": row.job,
            "status": row.status,
            "started_at": _as_text(row.started_at),
            "finished_at": _as_text(row.finished_at),
            "rows_affected": row.rows_affected,
            "detail": row.detail,
        }
        for row in recent_rows
    ]

    total = int(
        db.execute(
            select(func.count()).select_from(CadenceJobRun)
        ).scalar()
        or 0
    )

    status_rows = db.execute(
        select(CadenceJobRun.status, func.count())
        .group_by(CadenceJobRun.status)
    ).all()
    by_status: Dict[str, int] = {}
    for status, count in status_rows:
        by_status[status or "unknown"] = int(count)

    job_rows = db.execute(
        select(
            CadenceJobRun.job,
            func.count(),
            func.sum(
                case((CadenceJobRun.status == "failed", 1), else_=0)
            ),
            func.max(CadenceJobRun.finished_at),
        )
        .group_by(CadenceJobRun.job)
    ).all()
    by_job: Dict[str, Dict[str, Any]] = {}
    for job, job_total, failed, last_finished_at in job_rows:
        by_job[job or "unknown"] = {
            "total": int(job_total or 0),
            "failed": int(failed or 0),
            "last_finished_at": _as_text(last_finished_at),
        }

    return {
        "runs": runs,
        "total": total,
        "by_status": by_status,
        "by_job": by_job,
    }


# Compatibility aliases for routers using the common list/get naming conventions.
list_cadence_runs = get_cadence_runs
get_runs = get_cadence_runs
list_runs = get_cadence_runs


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import Depends, FastAPI
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
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="daily_scan",
                    status="success",
                    started_at=datetime(2024, 1, 1, 1, 0, 0),
                    finished_at=datetime(2024, 1, 1, 1, 1, 0),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=2,
                    job="daily_scan",
                    status="failed",
                    started_at=datetime(2024, 1, 2, 1, 0, 0),
                    finished_at=datetime(2024, 1, 2, 1, 1, 0),
                    rows_affected=0,
                    detail="error",
                ),
                CadenceJobRun(
                    id=3,
                    job="weekly_sync",
                    status="success",
                    started_at=datetime(2024, 1, 3, 1, 0, 0),
                    finished_at=datetime(2024, 1, 3, 1, 1, 0),
                    rows_affected=20,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=4,
                    job="weekly_sync",
                    status="success",
                    started_at=datetime(2024, 1, 4, 1, 0, 0),
                    finished_at=datetime(2024, 1, 4, 1, 1, 0),
                    rows_affected=25,
                    detail="ok",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/cadence/runs")
    def cadence_runs(db: Session = Depends(get_session)):
        return get_cadence_runs(db)

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(that_app).get("/api/cadence/runs")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 4
    assert payload["by_status"]["failed"] == 1
    assert "daily_scan" in payload["by_job"]
    assert "weekly_sync" in payload["by_job"]
    print("PASS")