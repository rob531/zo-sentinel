from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_cadence_job_volume

router = APIRouter(prefix="/api", tags=["cadence"])


class JobVolumeStats(BaseModel):
    job: str
    runs: int
    ok_runs: int
    failed_runs: int
    rows_affected_sum: int
    avg_duration_seconds: float | None
    max_duration_seconds: float | None
    last_started_at: datetime | None


class JobVolumeResponse(BaseModel):
    days: int
    jobs: list[JobVolumeStats]


@router.get(
    "/cadence/job-volume",
    response_model=JobVolumeResponse,
    summary="Get cadence job run volume and duration statistics",
)
def cadence_job_volume(
    days: Annotated[int, Query(ge=1)] = 7,
    session: Session = Depends(get_session),
) -> JobVolumeResponse:
    return get_cadence_job_volume(session=session, days=days)


def _run_self_test() -> None:
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.utcnow()
    with session_factory() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="job-a",
                    status="ok",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    job="job-a",
                    status="ok",
                    started_at=now - timedelta(minutes=10),
                    finished_at=None,
                    rows_affected=5,
                    detail=None,
                ),
                CadenceJobRun(
                    job="job-b",
                    status="error",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=0,
                    detail="failed",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/cadence/job-volume",
        params={"days": 7},
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["days"] == 7
    assert len(payload["jobs"]) == 2

    jobs = {item["job"]: item for item in payload["jobs"]}
    assert jobs["job-a"]["ok_runs"] == 2
    assert jobs["job-a"]["failed_runs"] == 0
    assert jobs["job-b"]["ok_runs"] == 0
    assert jobs["job-b"]["failed_runs"] == 1
    assert jobs["job-a"]["rows_affected_sum"] == 15
    assert jobs["job-a"]["avg_duration_seconds"] == 60.0
    assert jobs["job-a"]["max_duration_seconds"] == 60.0

    print("PASS")


if __name__ == "__main__":
    _run_self_test()