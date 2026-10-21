from __future__ import annotations

from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_rows_affected as _get_rows_affected
except ImportError:
    try:
        from .logic import get_cadence_job_rows_affected as _get_rows_affected
    except ImportError:
        from .logic import summarize_cadence_jobs as _get_rows_affected

try:
    from .contract import CadenceJobRowsAffectedResponse as ResponseModel
except ImportError:
    try:
        from .contract import RowsAffectedResponse as ResponseModel
    except ImportError:

        class CadenceJobRowsAffectedItem(BaseModel):
            job: str
            runs: int
            total_rows_affected: int
            avg_rows_affected: float
            succeeded: int
            failed: int
            avg_duration_seconds: float

        class ResponseModel(BaseModel):
            jobs: list[CadenceJobRowsAffectedItem]
            generated_at: str


router = APIRouter(prefix="/api", tags=["cadence"])


@router.get(
    "/cadence/jobs/rows-affected",
    response_model=ResponseModel,
    summary="Summarize rows affected by cadence jobs",
)
def get_rows_affected(
    session: Session = Depends(get_session),
    job: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1),
) -> dict:
    return _get_rows_affected(session, job, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="succeeded",
                    started_at=datetime(2025, 1, 1, 0, 0, 0),
                    finished_at=datetime(2025, 1, 1, 0, 0, 10),
                    rows_affected=5,
                    detail="ok",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=datetime(2025, 1, 2, 0, 0, 0),
                    finished_at=datetime(2025, 1, 2, 0, 0, 20),
                    rows_affected=None,
                    detail="failed",
                ),
                CadenceJobRun(
                    job="beta",
                    status="succeeded",
                    started_at=datetime(2025, 1, 3, 0, 0, 0),
                    finished_at=datetime(2025, 1, 3, 0, 0, 30),
                    rows_affected=7,
                    detail="ok",
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/cadence/jobs/rows-affected")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2

    failed_job = next(item for item in payload["jobs"] if item["failed"] == 1)
    assert failed_job["runs"] == 2
    assert failed_job["succeeded"] == 1
    assert failed_job["failed"] == 1
    assert failed_job["total_rows_affected"] == 5

    print("PASS")