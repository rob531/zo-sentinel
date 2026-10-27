from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_cadence_job_rows_affected_trend


router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class JobRowsAffectedSummary(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    rows_affected_total: int
    rows_affected_max: int


class JobRowsAffectedSeriesPoint(BaseModel):
    date: str
    job: str
    rows_affected: int
    runs: int


class CadenceJobRowsAffectedTrendResponse(BaseModel):
    days: int
    jobs: list[JobRowsAffectedSummary]
    series: list[JobRowsAffectedSeriesPoint]


@router.get(
    "/rows-affected",
    response_model=CadenceJobRowsAffectedTrendResponse,
)
async def cadence_job_rows_affected_trend(
    days: int = Query(default=14, ge=1, le=90),
    job: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    payload = get_cadence_job_rows_affected_trend(
        session=session,
        days=days,
        job=job,
    )
    if isawaitable(payload):
        payload = await payload
    return payload


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    day_one = now - timedelta(days=1)
    day_two = now - timedelta(days=2)
    seeded_rows = [
        CadenceJobRun(
            job="alpha",
            status="succeeded",
            started_at=day_one,
            finished_at=day_one + timedelta(minutes=1),
            rows_affected=10,
            detail="seed",
        ),
        CadenceJobRun(
            job="alpha",
            status="failed",
            started_at=day_one + timedelta(hours=1),
            finished_at=day_one + timedelta(hours=2),
            rows_affected=20,
            detail="seed",
        ),
        CadenceJobRun(
            job="alpha",
            status="succeeded",
            started_at=day_two,
            finished_at=day_two + timedelta(minutes=1),
            rows_affected=30,
            detail="seed",
        ),
        CadenceJobRun(
            job="beta",
            status="succeeded",
            started_at=day_one,
            finished_at=day_one + timedelta(minutes=1),
            rows_affected=5,
            detail="seed",
        ),
        CadenceJobRun(
            job="beta",
            status="failed",
            started_at=day_one + timedelta(hours=1),
            finished_at=day_one + timedelta(hours=2),
            rows_affected=7,
            detail="seed",
        ),
        CadenceJobRun(
            job="beta",
            status="succeeded",
            started_at=day_two,
            finished_at=day_two + timedelta(minutes=1),
            rows_affected=11,
            detail="seed",
        ),
    ]

    with testing_session() as db:
        db.add_all(seeded_rows)
        db.commit()

    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/jobs/rows-affected?days=14")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 14
        assert len(payload["jobs"]) == 2
        totals = {item["job"]: item["rows_affected_total"] for item in payload["jobs"]}
        assert totals["alpha"] == 60
        assert totals["beta"] == 23

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()