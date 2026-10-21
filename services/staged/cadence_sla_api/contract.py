from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class CadenceSlaJob(BaseModel):
    job: str
    runs: int
    failures: int
    success_rate: float
    last_started_at: datetime
    last_duration_seconds: Optional[float]
    avg_duration_seconds: float
    rows_affected_last: int
    breached: bool
    detail: Dict[str, Any] = Field(default_factory=dict)


class CadenceSlaResponse(BaseModel):
    hours: int
    generated_at: datetime
    jobs: List[CadenceSlaJob]
    job_count: int
    breached_count: int


router = APIRouter(prefix="/api")


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"Unsupported timestamp value: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode_detail(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    return {}


def _fetch_job_health(session: Session, hours: int) -> List[CadenceSlaJob]:
    if CadenceJobRun.__tablename__ != "cadence_job_runs":
        raise RuntimeError("CadenceJobRun does not map to cadence_job_runs")

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        sql = text(
            """
            WITH measured AS (
                SELECT
                    id,
                    job,
                    status,
                    started_at,
                    finished_at,
                    rows_affected,
                    detail,
                    EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_seconds
                FROM cadence_job_runs
                WHERE started_at >= CURRENT_TIMESTAMP
                    - (CAST(:hours AS INTEGER) * INTERVAL '1 hour')
            ),
            windowed AS (
                SELECT
                    id,
                    job,
                    status,
                    started_at,
                    finished_at,
                    rows_affected,
                    detail,
                    duration_seconds,
                    COUNT(*) OVER (PARTITION BY job) AS runs,
                    SUM(CASE WHEN status <> 'success' THEN 1 ELSE 0 END)
                        OVER (PARTITION BY job) AS failures,
                    AVG(duration_seconds) OVER (PARTITION BY job) AS avg_duration_seconds,
                    ROW_NUMBER() OVER (
                        PARTITION BY job
                        ORDER BY started_at DESC, id DESC
                    ) AS run_number
                FROM measured
            )
            SELECT
                job,
                runs,
                failures,
                started_at,
                duration_seconds,
                avg_duration_seconds,
                rows_affected,
                detail
            FROM windowed
            WHERE run_number = 1
            ORDER BY failures DESC, job ASC
            """
        )
        params = {"hours": hours}
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        sql = text(
            """
            WITH measured AS (
                SELECT
                    id,
                    job,
                    status,
                    started_at,
                    finished_at,
                    rows_affected,
                    detail,
                    (
                        (julianday(finished_at) - julianday(started_at)) * 86400.0
                    ) AS duration_seconds
                FROM cadence_job_runs
                WHERE started_at >= :cutoff
            ),
            windowed AS (
                SELECT
                    id,
                    job,
                    status,
                    started_at,
                    finished_at,
                    rows_affected,
                    detail,
                    duration_seconds,
                    COUNT(*) OVER (PARTITION BY job) AS runs,
                    SUM(CASE WHEN status <> 'success' THEN 1 ELSE 0 END)
                        OVER (PARTITION BY job) AS failures,
                    AVG(duration_seconds) OVER (PARTITION BY job) AS avg_duration_seconds,
                    ROW_NUMBER() OVER (
                        PARTITION BY job
                        ORDER BY started_at DESC, id DESC
                    ) AS run_number
                FROM measured
            )
            SELECT
                job,
                runs,
                failures,
                started_at,
                duration_seconds,
                avg_duration_seconds,
                rows_affected,
                detail
            FROM windowed
            WHERE run_number = 1
            ORDER BY failures DESC, job ASC
            """
        )
        params = {"cutoff": cutoff.replace(tzinfo=None)}

    rows = session.execute(sql, params).mappings().all()
    jobs: List[CadenceSlaJob] = []

    for row in rows:
        runs = int(row["runs"])
        failures = int(row["failures"])
        success_rate = (runs - failures) / runs if runs else 0.0
        duration = row["duration_seconds"]
        duration_seconds = float(duration) if duration is not None else None
        average = row["avg_duration_seconds"]
        avg_duration_seconds = float(average) if average is not None else 0.0
        breached = success_rate < 1.0 or (
            duration_seconds is not None and duration_seconds > 900.0
        )

        jobs.append(
            CadenceSlaJob(
                job=str(row["job"]),
                runs=runs,
                failures=failures,
                success_rate=success_rate,
                last_started_at=_as_datetime(row["started_at"]),
                last_duration_seconds=duration_seconds,
                avg_duration_seconds=avg_duration_seconds,
                rows_affected_last=int(row["rows_affected"] or 0),
                breached=breached,
                detail=_decode_detail(row["detail"]),
            )
        )

    return jobs


@router.get("/cadence/sla", response_model=CadenceSlaResponse)
def get_cadence_sla(
    hours: int = Query(default=24),
    session: Session = Depends(get_session),
) -> CadenceSlaResponse:
    bounded_hours = max(1, min(720, hours))
    jobs = _fetch_job_health(session, bounded_hours)

    return CadenceSlaResponse(
        hours=bounded_hours,
        generated_at=datetime.now(timezone.utc),
        jobs=jobs,
        job_count=len(jobs),
        breached_count=sum(1 for job in jobs if job.breached),
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with Session(engine) as session:
        session.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME NULL,
                    rows_affected INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NULL
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        session.execute(
            text(
                """
                INSERT INTO cadence_job_runs (
                    id, job, status, started_at, finished_at, rows_affected, detail
                ) VALUES (
                    :id, :job, :status, :started_at, :finished_at, :rows_affected, :detail
                )
                """
            ),
            [
                {
                    "id": 1,
                    "job": "failing-job",
                    "status": "failed",
                    "started_at": now - timedelta(hours=2),
                    "finished_at": now - timedelta(hours=2) + timedelta(seconds=60),
                    "rows_affected": 10,
                    "detail": json.dumps({"phase": "sync"}),
                },
                {
                    "id": 2,
                    "job": "failing-job",
                    "status": "success",
                    "started_at": now - timedelta(hours=1),
                    "finished_at": now - timedelta(hours=1) + timedelta(seconds=30),
                    "rows_affected": 20,
                    "detail": json.dumps({"phase": "sync"}),
                },
                {
                    "id": 3,
                    "job": "unfinished-job",
                    "status": "success",
                    "started_at": now - timedelta(minutes=30),
                    "finished_at": None,
                    "rows_affected": 5,
                    "detail": json.dumps({"phase": "verify"}),
                },
            ],
        )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/sla", params={"hours": 24})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["job_count"] == 2, payload

        jobs = {job["job"]: job for job in payload["jobs"]}
        failing_job = jobs["failing-job"]
        assert failing_job["failures"] == 1, failing_job
        assert failing_job["breached"] is True, failing_job

        unfinished_job = jobs["unfinished-job"]
        assert unfinished_job["last_duration_seconds"] is None, unfinished_job

    print("PASS")


if __name__ == "__main__":
    _run_self_test()