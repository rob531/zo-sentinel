from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class CadenceJobVolume(BaseModel):
    job: str
    run_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    total_rows_affected: int = Field(ge=0)
    avg_rows_affected: float = Field(ge=0)
    avg_duration_seconds: float = Field(ge=0)


class CadenceRowVolumeResponse(BaseModel):
    jobs: List[CadenceJobVolume]
    total_jobs: int = Field(ge=0)


router = APIRouter(prefix="/api/cadence", tags=["cadence"])


def get_cadence_row_volume(
    job: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> CadenceRowVolumeResponse:
    statement = select(
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.rows_affected,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
    )
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    rows = session.execute(statement).all()
    grouped: Dict[str, List[tuple]] = defaultdict(list)
    for row in rows:
        grouped[row.job].append(row)

    jobs: List[CadenceJobVolume] = []
    for job_name, records in sorted(grouped.items()):
        run_count = len(records)
        success_count = sum(1 for record in records if record.status == "success")
        failure_count = sum(
            1
            for record in records
            if record.status is not None and record.status != "success"
        )
        row_values = [int(record.rows_affected or 0) for record in records]
        total_rows_affected = sum(row_values)
        durations: List[float] = []
        for record in records:
            started_at = record.started_at
            finished_at = record.finished_at
            if started_at is not None and finished_at is not None:
                durations.append((finished_at - started_at).total_seconds())

        jobs.append(
            CadenceJobVolume(
                job=job_name,
                run_count=run_count,
                success_count=success_count,
                failure_count=failure_count,
                total_rows_affected=total_rows_affected,
                avg_rows_affected=(total_rows_affected / run_count) if run_count else 0.0,
                avg_duration_seconds=(sum(durations) / len(durations)) if durations else 0.0,
            )
        )

    return CadenceRowVolumeResponse(jobs=jobs, total_jobs=len(jobs))


get_row_volume = get_cadence_row_volume


@router.get("/row-volume", response_model=CadenceRowVolumeResponse)
def read_cadence_row_volume(
    job: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> CadenceRowVolumeResponse:
    return get_cadence_row_volume(job=job, session=session)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    CadenceJobRun.__table__.create(engine)

    with TestingSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="scan",
                    status="success",
                    started_at=datetime(2025, 1, 1, 0, 0, 0),
                    finished_at=datetime(2025, 1, 1, 0, 0, 10),
                    rows_affected=10,
                    detail="first scan",
                ),
                CadenceJobRun(
                    job="scan",
                    status="success",
                    started_at=datetime(2025, 1, 1, 0, 1, 0),
                    finished_at=datetime(2025, 1, 1, 0, 1, 20),
                    rows_affected=20,
                    detail="second scan",
                ),
                CadenceJobRun(
                    job="score",
                    status="failed",
                    started_at=datetime(2025, 1, 1, 0, 2, 0),
                    finished_at=datetime(2025, 1, 1, 0, 2, 5),
                    rows_affected=5,
                    detail="scoring failed",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = router.__class__ and __import__("fastapi").FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/row-volume")
        assert response.status_code == 200, response.text
        payload = response.json()

    assert payload["total_jobs"] == 2, payload
    jobs = {entry["job"]: entry for entry in payload["jobs"]}

    assert jobs["scan"]["run_count"] == 2, payload
    assert jobs["scan"]["total_rows_affected"] == 30, payload
    assert jobs["scan"]["success_count"] == 2, payload
    assert jobs["score"]["failure_count"] == 1, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()