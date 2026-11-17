"""logic.py -- the service's data/computation layer for cadence job runs."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"ok", "success", "succeeded", "completed", "complete"}


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> float:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return 0.0

    try:
        duration = (finished - started).total_seconds()
    except TypeError:
        if started.tzinfo is not None:
            started = started.replace(tzinfo=None)
        if finished.tzinfo is not None:
            finished = finished.replace(tzinfo=None)
        duration = (finished - started).total_seconds()

    return max(0.0, float(duration))


def get_cadence_runs(db: Session, hours: int = 24) -> List[Dict[str, Any]]:
    """Return runs grouped by job for the requested recent time window."""
    try:
        window_hours = max(0, int(hours))
    except (TypeError, ValueError):
        window_hours = 0

    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    rows = db.execute(
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    ).scalars().all()

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for run in rows:
        status = str(run.status or "").lower()
        grouped.setdefault(run.job, []).append(
            {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_seconds": _duration_seconds(run.started_at, run.finished_at),
                "rows_affected": run.rows_affected,
            }
        )

    result: List[Dict[str, Any]] = []
    for job, runs in grouped.items():
        success_count = sum(
            1 for run in runs if str(run["status"] or "").lower() in _SUCCESS_STATUSES
        )
        failure_count = sum(
            1 for run in runs if str(run["status"] or "").lower() not in _SUCCESS_STATUSES
        )
        result.append(
            {
                "job": job,
                "runs": runs,
                "success_count": success_count,
                "failure_count": failure_count,
            }
        )

    result.sort(key=lambda item: item["job"])
    return result


# Compatibility aliases for routers and service callers.
get_job_run_timeline = get_cadence_runs
get_timeline = get_cadence_runs
list_cadence_runs = get_cadence_runs
get_job_runs = get_cadence_runs


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="registry_scan",
                    status="ok",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=10,
                ),
                CadenceJobRun(
                    id=2,
                    job="registry_scan",
                    status="ok",
                    started_at=now - timedelta(minutes=20),
                    finished_at=now - timedelta(minutes=19),
                    rows_affected=12,
                ),
                CadenceJobRun(
                    id=3,
                    job="registry_scan",
                    status="failed",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=0,
                ),
                CadenceJobRun(
                    id=4,
                    job="scoring_wave",
                    status="ok",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=4,
                ),
            ]
        )
        session.commit()

        timeline = get_cadence_runs(session, hours=2)
        assert len(timeline) == 2
        registry = next(item for item in timeline if item["job"] == "registry_scan")
        assert registry["success_count"] == 2
        assert registry["failure_count"] == 1
        assert all(run["duration_seconds"] >= 0 for item in timeline for run in item["runs"])

    print("PASS")