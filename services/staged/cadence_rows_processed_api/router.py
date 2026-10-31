from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_rows_processed
except ImportError:  # pragma: no cover - compatibility with older logic exports
    from .logic import aggregate_rows_processed as get_rows_processed


router = APIRouter()


class CadenceJobRowsProcessed(BaseModel):
    job: str
    runs: int
    failed_runs: int
    rows_total: int
    rows_max: int | None
    mean_duration_s: float | None
    last_finished_at: datetime | None


class CadenceRowsProcessedResponse(BaseModel):
    days: int
    jobs: list[CadenceJobRowsProcessed]


def _as_jobs(result: Any) -> list[dict[str, Any]]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    if isinstance(result, dict):
        return list(result.get("jobs", []))
    if isinstance(result, list):
        return list(result)
    return []


@router.get(
    "/cadence/rows-processed",
    response_model=CadenceRowsProcessedResponse,
)
def get_cadence_rows_processed(
    days: int = 30,
    db: Session = Depends(get_session),
) -> CadenceRowsProcessedResponse:
    result = get_rows_processed(db, days)
    return CadenceRowsProcessedResponse(
        days=days,
        jobs=_as_jobs(result),
    )


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    rows_affected INTEGER,
                    detail JSON
                )
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'job_a', 'success', :started, :finished, 10, '{}'),
                    (2, 'job_a', 'failed', :started, :finished, NULL, '{}'),
                    (3, 'job_a', 'success', :started, :finished, 5, '{}'),
                    (4, 'job_b', 'success', :started, :finished, 7, '{}'),
                    (5, 'job_b', 'success', :started, :finished, NULL, '{}')
                """
            ),
            {"started": now - timedelta(days=1), "finished": now},
        )

    test_db = TestingSession()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: test_db

    try:
        with TestClient(app) as client:
            response = client.get("/api/cadence/rows-processed?days=30")
            assert response.status_code == 200
            payload = response.json()
            jobs = {item["job"]: item for item in payload["jobs"]}
            assert jobs["job_a"]["runs"] == 3
            assert jobs["job_b"]["runs"] == 2
            assert jobs["job_a"]["rows_total"] == 15
            assert jobs["job_b"]["rows_total"] == 7
            assert jobs["job_a"]["failed_runs"] == 1
            assert jobs["job_b"]["failed_runs"] == 0
    finally:
        test_db.close()
        engine.dispose()

    print("PASS")