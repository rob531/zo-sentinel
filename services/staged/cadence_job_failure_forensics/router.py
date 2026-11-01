"""HTTP surface for cadence job failure forensics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_failure_forensics

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class JobRunResponse(BaseModel):
    id: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    rows_affected: int | None = None
    detail: str | None = None


class JobFailureResponse(BaseModel):
    job: str
    days: int
    totals: dict[str, int]
    runs: list[JobRunResponse]
    failure_count: int


@router.get("/{job}/failures", response_model=JobFailureResponse)
def get_job_failures(
    job: str,
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_session),
) -> JobFailureResponse:
    report: Any = get_failure_forensics(db, job, days)
    return JobFailureResponse.model_validate(report)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="nightly-import",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=12),
                    rows_affected=100,
                    detail=None,
                ),
                CadenceJobRun(
                    job="nightly-import",
                    status="failure",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=4),
                    rows_affected=0,
                    detail="connection reset",
                ),
                CadenceJobRun(
                    job="nightly-import",
                    status="error",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3) + timedelta(seconds=2),
                    rows_affected=0,
                    detail="invalid response",
                ),
                CadenceJobRun(
                    job="nightly-import",
                    status="success",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4) + timedelta(seconds=8),
                    rows_affected=80,
                    detail=None,
                ),
                CadenceJobRun(
                    job="other-job",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=3),
                    rows_affected=5,
                    detail=None,
                ),
            ]
        )
        session.commit()

    application = FastAPI()
    application.include_router(router)

    def override_get_session() -> Any:
        with Session(engine) as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    client = TestClient(application)
    response = client.get("/api/cadence/jobs/nightly-import/failures?days=30")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"] == "nightly-import"
    assert payload["failure_count"] == 2
    assert all(run["id"] != 5 for run in payload["runs"])
    assert payload["totals"] == {"success": 2, "failure": 1, "error": 1}
    print("PASS")


if __name__ == "__main__":
    _self_test()