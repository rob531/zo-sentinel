from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if started is None or finished is None:
        return None

    if (started.tzinfo is None) != (finished.tzinfo is None):
        if started.tzinfo is not None:
            started = started.replace(tzinfo=None)
        if finished.tzinfo is not None:
            finished = finished.replace(tzinfo=None)

    return (finished - started).total_seconds()


@router.get("/cadence/jobs/status-mix")
def get_cadence_job_status_mix(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    summary_rows = session.execute(
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            func.count(CadenceJobRun.id).label("runs"),
            func.coalesce(func.sum(CadenceJobRun.rows_affected), 0).label(
                "rows_affected_total"
            ),
        )
        .group_by(CadenceJobRun.job, CadenceJobRun.status)
        .order_by(CadenceJobRun.job, CadenceJobRun.status)
    ).all()

    duration_rows = session.execute(
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
        ).order_by(CadenceJobRun.id)
    ).all()

    durations: Dict[tuple, List[float]] = {}
    for job, status, started_at, finished_at in duration_rows:
        seconds = _duration_seconds(started_at, finished_at)
        if seconds is not None:
            durations.setdefault((job, status), []).append(seconds)

    jobs: List[Dict[str, Any]] = []
    for job, status, runs, rows_affected_total in summary_rows:
        group_durations = durations.get((job, status), [])
        avg_seconds = (
            round(sum(group_durations) / len(group_durations), 3)
            if group_durations
            else 0.0
        )
        jobs.append(
            {
                "job": job,
                "status": status,
                "runs": int(runs),
                "rows_affected_total": int(rows_affected_total or 0),
                "avg_seconds": avg_seconds,
            }
        )

    return {
        "jobs": jobs,
        "total_runs": sum(item["runs"] for item in jobs),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    base = datetime(2025, 1, 1, 12, 0, 0)
    with TestingSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=base,
                    finished_at=base + __import__("datetime").timedelta(seconds=2),
                    rows_affected=3,
                ),
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=base,
                    finished_at=base + __import__("datetime").timedelta(seconds=4),
                    rows_affected=5,
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=base,
                    finished_at=base + __import__("datetime").timedelta(seconds=6),
                    rows_affected=0,
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=base,
                    finished_at=base + __import__("datetime").timedelta(seconds=1),
                    rows_affected=2,
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=base,
                    finished_at=base + __import__("datetime").timedelta(seconds=3),
                    rows_affected=5,
                ),
                CadenceJobRun(
                    job="beta",
                    status="running",
                    started_at=base,
                    finished_at=None,
                    rows_affected=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/cadence/jobs/status-mix")
        assert response.status_code == 200
        payload = response.json()

    assert payload["total_runs"] == 6
    assert sum(item["runs"] for item in payload["jobs"]) == payload["total_runs"]
    alpha_success = next(
        item
        for item in payload["jobs"]
        if item["job"] == "alpha" and item["status"] == "success"
    )
    assert alpha_success["runs"] == 2
    assert alpha_success["rows_affected_total"] == 8
    assert alpha_success["avg_seconds"] == 3.0
    print("PASS")


if __name__ == "__main__":
    _self_test()