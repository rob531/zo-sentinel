from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

_SUCCESS_STATUSES = {
    "success",
    "succeeded",
    "successful",
    "ok",
    "complete",
    "completed",
}


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_detail(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_success(status: Any) -> bool:
    return str(status or "").strip().lower() in _SUCCESS_STATUSES


def _serialize_run(run: CadenceJobRun) -> dict[str, Any]:
    return {
        "id": int(run.id),
        "status": str(run.status),
        "started_at": _isoformat(run.started_at),
        "finished_at": _isoformat(run.finished_at),
        "rows_affected": int(run.rows_affected or 0),
        "detail": _parse_detail(run.detail),
    }


def _latest_failure(session: Session, job: str) -> Optional[dict[str, Any]]:
    statement = (
        select(CadenceJobRun)
        .where(
            CadenceJobRun.job == job,
            func.lower(CadenceJobRun.status).notin_(_SUCCESS_STATUSES),
        )
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .limit(1)
    )
    run = session.execute(statement).scalars().first()
    if run is None:
        return None
    return {
        "id": int(run.id),
        "started_at": _isoformat(run.started_at),
        "detail": _parse_detail(run.detail),
    }


def list_job_runs(
    job: str,
    limit: int = 20,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    safe_limit = max(0, int(limit))
    statement = (
        select(CadenceJobRun)
        .where(CadenceJobRun.job == job)
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .limit(safe_limit)
    )
    rows = list(session.execute(statement).scalars().all())

    succeeded = sum(1 for row in rows if _is_success(row.status))
    failed = len(rows) - succeeded
    total = len(rows)
    success_rate = succeeded / total if total else 0.0

    return {
        "job": str(job),
        "runs": [_serialize_run(row) for row in rows],
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "success_rate": success_rate,
        "latest_failure": _latest_failure(session, job),
    }


def get_latest_failure(
    job: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {
        "job": str(job),
        "latest_failure": _latest_failure(session, job),
    }


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_session():
        with TestSession() as session:
            yield session

    with TestSession() as session:
        base = datetime(2025, 1, 1, 12, 0, 0)
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="risk_ranker",
                    status="success",
                    started_at=base,
                    finished_at=base,
                    rows_affected=10,
                    detail={"batch": 1},
                ),
                CadenceJobRun(
                    id=2,
                    job="risk_ranker",
                    status="success",
                    started_at=base.replace(minute=1),
                    finished_at=base.replace(minute=1),
                    rows_affected=20,
                    detail={"batch": 2},
                ),
                CadenceJobRun(
                    id=3,
                    job="risk_ranker",
                    status="success",
                    started_at=base.replace(minute=2),
                    finished_at=base.replace(minute=2),
                    rows_affected=30,
                    detail={"batch": 3},
                ),
                CadenceJobRun(
                    id=4,
                    job="risk_ranker",
                    status="failure",
                    started_at=base.replace(minute=3),
                    finished_at=base.replace(minute=3),
                    rows_affected=0,
                    detail={"error": "ranking failed"},
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/cadence/jobs/{job}/runs")
    def job_runs(
        job: str,
        limit: int = 20,
        session: Session = Depends(override_session),
    ):
        return list_job_runs(job=job, limit=limit, session=session)

    @that_app.get("/api/cadence/jobs/{job}/latest-failure")
    def latest_failure(
        job: str,
        session: Session = Depends(override_session),
    ):
        return get_latest_failure(job=job, session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/jobs/risk_ranker/runs", params={"limit": 20})
        assert response.status_code == 200
        payload = response.json()
        assert payload["job"] == "risk_ranker"
        assert payload["total"] == 4
        assert payload["succeeded"] == 3
        assert payload["failed"] == 1
        assert payload["success_rate"] == 0.75
        assert payload["latest_failure"] is not None
        assert payload["latest_failure"]["id"] == 4
        assert payload["latest_failure"]["detail"] == {"error": "ranking failed"}

        response = client.get("/api/cadence/jobs/risk_ranker/latest-failure")
        assert response.status_code == 200
        assert response.json()["latest_failure"]["id"] == 4

    test_engine.dispose()
    print("PASS")