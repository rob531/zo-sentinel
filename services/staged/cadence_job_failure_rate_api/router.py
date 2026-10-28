from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_failure_rate

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class CadenceJobFailureRate(BaseModel):
    job: str
    runs: int
    ok: int
    failed: int
    other: int
    failure_rate: float
    avg_duration_seconds: Optional[float]
    rows_affected_total: int
    last_status: Optional[str]
    last_started_at: Optional[datetime]


class CadenceFailureTotals(BaseModel):
    runs: int
    ok: int
    failed: int
    failure_rate: float


class CadenceJobFailureRateResponse(BaseModel):
    hours: int
    jobs: list[CadenceJobFailureRate]
    totals: CadenceFailureTotals


@router.get("/failure-rate", response_model=CadenceJobFailureRateResponse)
def cadence_job_failure_rate(
    hours: int = Query(168, ge=1, le=8760),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> CadenceJobFailureRateResponse:
    payload = get_failure_rate(session, hours, limit)
    return CadenceJobFailureRateResponse.model_validate(payload)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    CadenceJobRun.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="SUCCESS",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="completed",
                    started_at=now - timedelta(minutes=20),
                    finished_at=now - timedelta(minutes=18),
                    rows_affected=20,
                    detail={},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    job="beta",
                    status="pending",
                    started_at=now - timedelta(minutes=5),
                    finished_at=None,
                    rows_affected=None,
                    detail={},
                ),
            ]
        )
        session.commit()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/cadence/jobs/failure-rate",
        params={"hours": 168, "limit": 100},
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["hours"] == 168
    assert payload["totals"]["runs"] == 4

    jobs = {item["job"]: item for item in payload["jobs"]}
    assert jobs["alpha"]["runs"] == 3
    assert jobs["alpha"]["ok"] == 2
    assert jobs["alpha"]["failed"] == 1
    assert jobs["alpha"]["failure_rate"] == 1 / 3
    assert jobs["alpha"]["avg_duration_seconds"] is not None
    assert jobs["beta"]["avg_duration_seconds"] is None

    print("PASS")


if __name__ == "__main__":
    _self_test()