from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


class CadenceJobStatusSummary(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    statuses: Dict[str, int]
    last_run_started_at: Optional[str]
    last_run_finished_at: Optional[str]
    median_duration_seconds: float
    total_rows_affected: int


class CadenceJobStatusTotals(BaseModel):
    runs: int
    failed: int
    failure_rate: float


class CadenceJobStatusMixResponse(BaseModel):
    hours: int
    jobs: List[CadenceJobStatusSummary]
    totals: CadenceJobStatusTotals


JobStatusSummary = CadenceJobStatusSummary
StatusTotals = CadenceJobStatusTotals
JobStatusMixResponse = CadenceJobStatusMixResponse
StatusMixResponse = CadenceJobStatusMixResponse


def _parse_timestamp(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _iso_timestamp(value: object) -> Optional[str]:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def _duration_seconds(started_at: object, finished_at: object) -> Optional[float]:
    started = _parse_timestamp(started_at)
    finished = _parse_timestamp(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is not None:
        started = started.astimezone(timezone.utc).replace(tzinfo=None)
    if finished.tzinfo is not None:
        finished = finished.astimezone(timezone.utc).replace(tzinfo=None)

    return max(0.0, (finished - started).total_seconds())


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


def summarize_cadence_job_status_mix(
    session: Session,
    hours: int = 24,
    job: Optional[str] = None,
) -> CadenceJobStatusMixResponse:
    if hours < 1 or hours > 168:
        raise ValueError("hours must be between 1 and 168")

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    rows = session.execute(statement).scalars().all()
    grouped: Dict[str, List[CadenceJobRun]] = {}
    for row in rows:
        grouped.setdefault(str(row.job), []).append(row)

    summaries: List[CadenceJobStatusSummary] = []
    total_runs = 0
    total_failed = 0

    for job_name in sorted(grouped):
        job_rows = grouped[job_name]
        statuses: Dict[str, int] = {}
        succeeded = 0
        failed = 0
        durations: List[float] = []
        last_started: Optional[datetime] = None
        last_finished: Optional[datetime] = None
        rows_affected = 0

        for row in job_rows:
            status = "unknown" if row.status is None else str(row.status)
            statuses[status] = statuses.get(status, 0) + 1
            if status.lower() == "ok":
                succeeded += 1
            if status.lower() == "failed":
                failed += 1

            started = _parse_timestamp(row.started_at)
            finished = _parse_timestamp(row.finished_at)
            if started is not None and (last_started is None or started > last_started):
                last_started = started
            if finished is not None and (last_finished is None or finished > last_finished):
                last_finished = finished

            duration = _duration_seconds(row.started_at, row.finished_at)
            if duration is not None:
                durations.append(duration)

            if row.rows_affected is not None:
                rows_affected += int(row.rows_affected)

        total_runs += len(job_rows)
        total_failed += failed
        summaries.append(
            CadenceJobStatusSummary(
                job=job_name,
                runs=len(job_rows),
                succeeded=succeeded,
                failed=failed,
                statuses=statuses,
                last_run_started_at=_iso_timestamp(last_started),
                last_run_finished_at=_iso_timestamp(last_finished),
                median_duration_seconds=_median(durations),
                total_rows_affected=rows_affected,
            )
        )

    return CadenceJobStatusMixResponse(
        hours=hours,
        jobs=summaries,
        totals=CadenceJobStatusTotals(
            runs=total_runs,
            failed=total_failed,
            failure_rate=(total_failed / total_runs) if total_runs else 0.0,
        ),
    )


def get_status_mix(
    session: Session = Depends(get_session),
    hours: int = 24,
    job: Optional[str] = None,
    db: Optional[Session] = None,
) -> CadenceJobStatusMixResponse:
    actual_session = db if db is not None else session
    return summarize_cadence_job_status_mix(actual_session, hours=hours, job=job)


get_cadence_job_status_mix = get_status_mix
get_cadence_status_mix = get_status_mix
get_job_status_mix = get_status_mix


if __name__ == "__main__":
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
                    detail TEXT
                )
                """
            )
        )
        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'a', 'ok', :started, :finished, 2, NULL),
                    (2, 'a', 'ok', :started, :finished, 3, NULL),
                    (3, 'a', 'failed', :started, :finished, 0, NULL),
                    (4, 'b', 'ok', :started, :finished, 1, NULL)
                """
            ),
            {
                "started": now,
                "finished": now + timedelta(seconds=1),
            },
        )

    testing_session = sessionmaker(bind=engine)()

    def override_get_session():
        yield testing_session

    local_app = FastAPI()

    @local_app.get("/api/cadence/jobs/status-mix")
    def status_mix_endpoint(
        hours: int = 24,
        job: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        return summarize_cadence_job_status_mix(session, hours=hours, job=job)

    local_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(local_app).get("/api/cadence/jobs/status-mix")
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["jobs"]) == 2
    assert next(item for item in payload["jobs"] if item["job"] == "a")["failed"] == 1
    assert payload["totals"]["runs"] == 4
    assert payload["totals"]["failure_rate"] == 0.25
    assert isinstance(payload["jobs"][0]["median_duration_seconds"], float)
    assert payload["jobs"][0]["median_duration_seconds"] >= 0.0
    print("PASS")