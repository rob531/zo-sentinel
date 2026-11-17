from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceJobLedgerEntry(BaseModel):
    job: str
    last_status: Optional[str]
    last_started_at: Optional[str]
    last_finished_at: Optional[str]
    duration_seconds: Optional[float]
    last_rows_affected: Optional[int]
    runs_total: int
    runs_failed: int
    runs_ok: int


class CadenceJobLedgerResponse(BaseModel):
    jobs: List[CadenceJobLedgerEntry]


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _format_datetime(value) -> Optional[str]:
    value = _as_datetime(value)
    return value.isoformat() if value is not None else None


def list_cadence_jobs(
    session: Session,
    job: Optional[str] = None,
    limit: int = 50,
) -> List[CadenceJobLedgerEntry]:
    limit = max(1, min(int(limit), 200))

    statement = select(CadenceJobRun)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)
    statement = statement.order_by(
        CadenceJobRun.started_at.desc(),
        CadenceJobRun.id.desc(),
    )

    runs = session.execute(statement).scalars().all()
    grouped: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    entries = []
    successful_statuses = {"ok", "success", "completed"}
    for job_name, job_runs in grouped.items():
        latest = job_runs[0]
        started_at = _as_datetime(latest.started_at)
        finished_at = _as_datetime(latest.finished_at)
        duration = None
        if started_at is not None and finished_at is not None:
            duration = (finished_at - started_at).total_seconds()

        runs_total = len(job_runs)
        runs_ok = sum(1 for run in job_runs if run.status in successful_statuses)
        runs_failed = runs_total - runs_ok

        entries.append(
            CadenceJobLedgerEntry(
                job=job_name,
                last_status=latest.status,
                last_started_at=_format_datetime(latest.started_at),
                last_finished_at=_format_datetime(latest.finished_at),
                duration_seconds=duration,
                last_rows_affected=latest.rows_affected,
                runs_total=runs_total,
                runs_failed=runs_failed,
                runs_ok=runs_ok,
            )
        )

    entries.sort(key=lambda entry: entry.job)
    return entries[:limit]


@router.get(
    "/cadence/jobs",
    response_model=CadenceJobLedgerResponse,
)
def get_cadence_jobs(
    job: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
    session: Session = Depends(get_session),
) -> CadenceJobLedgerResponse:
    return CadenceJobLedgerResponse(
        jobs=list_cadence_jobs(session=session, job=job, limit=limit)
    )


def _self_test() -> None:
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    CadenceJobRun.__table__.create(engine)
    started_at = datetime(2024, 1, 1, 12, 0, 0)
    with testing_session_factory() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="daily-report",
                    status="ok",
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=3),
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    id=2,
                    job="nightly-sync",
                    status="failed",
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=7),
                    rows_affected=0,
                    detail={"error": "test failure"},
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with testing_session_factory() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(local_app)

    response = client.get("/api/cadence/jobs")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2

    failed_job = next(
        item for item in payload["jobs"] if item["job"] == "nightly-sync"
    )
    assert failed_job["runs_failed"] == 1
    assert failed_job["duration_seconds"] is not None

    limited_response = client.get("/api/cadence/jobs", params={"limit": 1})
    assert limited_response.status_code == 200
    assert len(limited_response.json()["jobs"]) == 1

    filtered_response = client.get(
        "/api/cadence/jobs", params={"job": "daily-report"}
    )
    assert filtered_response.status_code == 200
    assert len(filtered_response.json()["jobs"]) == 1
    assert filtered_response.json()["jobs"][0]["job"] == "daily-report"

    print("PASS")


if __name__ == "__main__":
    _self_test()