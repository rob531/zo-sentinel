from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun
from .logic import get_cadence_job_runs_trend

router = APIRouter(prefix="/api/cadence/runs")


class JobTrend(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    rows_affected: int
    avg_duration_seconds: Optional[float]


class TrendPoint(BaseModel):
    date: str
    status: str
    count: int


class TrendResponse(BaseModel):
    days: int
    jobs: list[JobTrend]
    series: list[TrendPoint]


@router.get("/trend", response_model=TrendResponse)
async def trend(
    days: int = Query(default=14, ge=1, le=365),
    job: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    result = get_cadence_job_runs_trend(session, days=days, job=job)
    if inspect.isawaitable(result):
        result = await result

    payload = dict(result)
    payload.setdefault("days", days)
    return payload


def _run_self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)

    now = datetime.utcnow().replace(microsecond=0)
    rows = [
        CadenceJobRun(
            job="scanner",
            status="failed",
            started_at=now - timedelta(days=1),
            finished_at=None,
            rows_affected=0,
            detail={},
        ),
        CadenceJobRun(
            job="scanner",
            status="succeeded",
            started_at=now,
            finished_at=now + timedelta(seconds=12),
            rows_affected=10,
            detail={},
        ),
        CadenceJobRun(
            job="indexer",
            status="succeeded",
            started_at=now - timedelta(days=1),
            finished_at=now - timedelta(days=1) + timedelta(seconds=8),
            rows_affected=20,
            detail={},
        ),
        CadenceJobRun(
            job="indexer",
            status="failed",
            started_at=now,
            finished_at=None,
            rows_affected=0,
            detail={},
        ),
        CadenceJobRun(
            job="reporter",
            status="succeeded",
            started_at=now - timedelta(days=1),
            finished_at=now - timedelta(days=1) + timedelta(seconds=4),
            rows_affected=5,
            detail={},
        ),
        CadenceJobRun(
            job="reporter",
            status="succeeded",
            started_at=now,
            finished_at=now + timedelta(seconds=6),
            rows_affected=7,
            detail={},
        ),
    ]

    with Session(engine) as session:
        session.add_all(rows)
        session.commit()

        def override_get_session():
            with Session(engine) as test_session:
                yield test_session

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/cadence/runs/trend",
                params={"days": 2, "job": "scanner"},
            )
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 2
            assert payload["jobs"]
            assert payload["series"]

            failed_points = [
                point for point in payload["series"] if point["status"] == "failed"
            ]
            assert len(failed_points) == 1
            assert failed_points[0]["count"] == 1

            scanner = next(item for item in payload["jobs"] if item["job"] == "scanner")
            assert scanner["avg_duration_seconds"] is not None
            assert scanner["runs"] == 2
            assert scanner["succeeded"] == 1
            assert scanner["failed"] == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()