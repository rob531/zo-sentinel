from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api")


def _iso8601(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _detail_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _duration_seconds(
    started_at: datetime | None, finished_at: datetime | None
) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


def list_jobs(session: Session) -> dict:
    runs = session.execute(
        select(CadenceJobRun).order_by(
            CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc()
        )
    ).scalars().all()

    jobs: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        jobs.setdefault(run.job, []).append(run)

    roster = []
    for job in sorted(jobs):
        job_runs = jobs[job]
        last_run = job_runs[0] if job_runs else None
        roster.append(
            {
                "job": job,
                "run_count": len(job_runs),
                "last_status": last_run.status if last_run else None,
                "last_run_at": _iso8601(last_run.started_at) if last_run else None,
            }
        )

    return {"jobs": roster}


def get_job_history(
    job: str, limit: int = 50, session: Session | None = None
) -> dict:
    if session is None:
        raise RuntimeError("A database session is required")

    runs = session.execute(
        select(CadenceJobRun)
        .where(CadenceJobRun.job == job)
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .limit(limit)
    ).scalars().all()

    successful = sum(1 for run in runs if (run.status or "").lower() == "ok")
    success_rate = round(successful / len(runs), 2) if runs else 0.0

    output_runs = []
    for run in runs:
        output_runs.append(
            {
                "id": run.id,
                "status": run.status,
                "started_at": _iso8601(run.started_at),
                "finished_at": _iso8601(run.finished_at),
                "duration_seconds": _duration_seconds(
                    run.started_at, run.finished_at
                ),
                "rows_affected": run.rows_affected,
                "detail": _detail_dict(run.detail),
            }
        )

    return {
        "job": job,
        "count": len(output_runs),
        "success_rate": success_rate,
        "last_run_at": _iso8601(runs[0].started_at) if runs else None,
        "runs": output_runs,
    }


@router.get("/cadence/jobs")
def cadence_jobs(session: Session = Depends(get_session)) -> dict:
    return list_jobs(session)


@router.get("/cadence/jobs/{job}/history")
def cadence_job_history(
    job: str,
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    return get_job_history(job, limit, session)


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    local_app.dependency_overrides[get_session] = override_get_session

    started = datetime(2024, 1, 1, 12, 0, 0)
    with session_factory() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="mcp_scanner",
                    status="ok",
                    started_at=started,
                    finished_at=started.replace(second=30),
                    rows_affected=10,
                    detail={"source": "seed"},
                ),
                CadenceJobRun(
                    job="mcp_scanner",
                    status="ok",
                    started_at=started.replace(minute=1),
                    finished_at=started.replace(minute=1, second=45),
                    rows_affected=12,
                    detail={"source": "seed"},
                ),
                CadenceJobRun(
                    job="mcp_scanner",
                    status="error",
                    started_at=started.replace(minute=2),
                    finished_at=started.replace(minute=2, second=10),
                    rows_affected=None,
                    detail={"source": "seed"},
                ),
            ]
        )
        session.commit()

    client = TestClient(local_app)

    response = client.get("/api/cadence/jobs")
    assert response.status_code == 200
    assert "mcp_scanner" in [item["job"] for item in response.json()["jobs"]]

    response = client.get("/api/cadence/jobs/mcp_scanner/history")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert abs(payload["success_rate"] - 0.67) <= 0.01
    error_run = next(run for run in payload["runs"] if run["status"] == "error")
    assert error_run["rows_affected"] is None

    print("PASS")


if __name__ == "__main__":
    _run_self_test()