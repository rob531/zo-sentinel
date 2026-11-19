"""HTTP surface for the cadence job failure digest service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

from .logic import build_failure_digest

router = APIRouter(prefix="/api", tags=["cadence"])


class JobFailureDigest(BaseModel):
    job: str
    runs: int
    failures: int
    partials: int
    success_rate: float
    rows_affected_avg: float | None
    last_failure_at: datetime | None
    last_error: str | None


class FailureDigestResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    jobs: list[JobFailureDigest]
    worst_jobs: list[str]


@router.get("/cadence/failure-digest", response_model=FailureDigestResponse)
def get_failure_digest(
    hours: int = Query(default=24, ge=1, le=720),
    db: Session = Depends(get_session),
) -> FailureDigestResponse:
    result: dict[str, Any] = build_failure_digest(db, hours=hours)
    return FailureDigestResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=12,
                    detail=None,
                ),
                CadenceJobRun(
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=0,
                    detail={"error": "boom"},
                ),
                CadenceJobRun(
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=1),
                    rows_affected=8,
                    detail=None,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    response = TestClient(test_app).get("/api/cadence/failure-digest")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["jobs"]) == 2, payload
    failing = next(job for job in payload["jobs"] if job["job"] == "failing-job")
    assert failing["failures"] == 1, payload
    assert failing["last_error"] == "boom", payload
    assert payload["worst_jobs"][0] == "failing-job", payload

    print("PASS")