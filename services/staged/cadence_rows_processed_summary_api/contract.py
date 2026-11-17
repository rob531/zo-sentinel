from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class JobRowsProcessedSummary(BaseModel):
    job: str
    runs: int
    success_runs: int
    failed_runs: int
    rows_affected: int
    avg_rows_per_success_run: float
    avg_duration_seconds: float


class SlowestJob(BaseModel):
    job: str
    avg_duration_seconds: float


class RowsProcessedSummaryResponse(BaseModel):
    days: int
    total_runs: int
    total_rows_affected: int
    by_job: list[JobRowsProcessedSummary]
    slowest: list[SlowestJob]


router = APIRouter(prefix="/api")


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(run: CadenceJobRun) -> Optional[float]:
    if run.started_at is None or run.finished_at is None:
        return None
    return max(
        0.0,
        (_as_aware(run.finished_at) - _as_aware(run.started_at)).total_seconds(),
    )


@router.get(
    "/cadence/rows-processed",
    response_model=RowsProcessedSummaryResponse,
)
def get_rows_processed_summary(
    days: int = Query(7, ge=1, le=90),
    job: Optional[str] = Query(None),
    db: Session = Depends(get_session),
) -> RowsProcessedSummaryResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    runs = list(db.execute(statement).scalars().all())

    grouped = defaultdict(list)
    total_rows_affected = 0

    for run in runs:
        grouped[run.job].append(run)
        total_rows_affected += run.rows_affected or 0

    by_job = []
    slowest = []

    for job_name, job_runs in grouped.items():
        success_runs = [
            run for run in job_runs if (run.status or "").strip().lower() == "success"
        ]
        failed_runs = len(job_runs) - len(success_runs)

        rows_affected = sum(run.rows_affected or 0 for run in job_runs)
        successful_rows = sum(run.rows_affected or 0 for run in success_runs)
        durations = [
            duration
            for duration in (_duration_seconds(run) for run in job_runs)
            if duration is not None
        ]
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        by_job.append(
            JobRowsProcessedSummary(
                job=job_name,
                runs=len(job_runs),
                success_runs=len(success_runs),
                failed_runs=failed_runs,
                rows_affected=rows_affected,
                avg_rows_per_success_run=(
                    successful_rows / len(success_runs) if success_runs else 0.0
                ),
                avg_duration_seconds=avg_duration,
            )
        )
        slowest.append(
            SlowestJob(job=job_name, avg_duration_seconds=avg_duration)
        )

    by_job.sort(key=lambda item: item.job)
    slowest.sort(key=lambda item: item.avg_duration_seconds, reverse=True)

    return RowsProcessedSummaryResponse(
        days=days,
        total_runs=len(runs),
        total_rows_affected=total_rows_affected,
        by_job=by_job,
        slowest=slowest,
    )


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with testing_session() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3) + timedelta(seconds=10),
                    rows_affected=100,
                    detail="accepted",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=20),
                    rows_affected=50,
                    detail="accepted",
                ),
                CadenceJobRun(
                    job="beta",
                    status="failed",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=30),
                    rows_affected=0,
                    detail="failed",
                ),
                CadenceJobRun(
                    job="gamma",
                    status="success",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=30) + timedelta(seconds=40),
                    rows_affected=25,
                    detail="accepted",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/rows-processed")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_runs"] == 4
        assert payload["total_rows_affected"] == 175
        alpha = next(entry for entry in payload["by_job"] if entry["job"] == "alpha")
        assert alpha["success_runs"] == 2
        assert alpha["rows_affected"] == 150

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()