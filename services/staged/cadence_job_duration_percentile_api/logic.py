from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


class CadenceJobDurationItem(BaseModel):
    job: str
    runs: int
    failures: int
    failure_rate: float
    min_seconds: Optional[float]
    median_seconds: Optional[float]
    p90_seconds: Optional[float]
    max_seconds: Optional[float]
    rows_affected_total: int


class CadenceJobDurationResponse(BaseModel):
    generated_at: datetime
    jobs: List[CadenceJobDurationItem]


router = APIRouter(prefix="/api", tags=["cadence"])


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is not None and finished.tzinfo is None:
        finished = finished.replace(tzinfo=started.tzinfo)
    elif finished.tzinfo is not None and started.tzinfo is None:
        started = started.replace(tzinfo=finished.tzinfo)

    return (finished - started).total_seconds()


def _is_failure(status: Any) -> bool:
    if status is None:
        return False
    return str(status).strip().lower() in {
        "failed",
        "failure",
        "error",
        "errored",
    }


def _percentile_90(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.90) - 1)
    return ordered[index]


def summarize_job_runs(rows: List[CadenceJobRun]) -> List[CadenceJobDurationItem]:
    grouped: Dict[str, List[CadenceJobRun]] = {}
    for row in rows:
        grouped.setdefault(row.job, []).append(row)

    summaries: List[CadenceJobDurationItem] = []
    for job in sorted(grouped):
        job_rows = grouped[job]
        durations = [
            duration
            for duration in (
                _duration_seconds(row.started_at, row.finished_at)
                for row in job_rows
            )
            if duration is not None
        ]
        failures = sum(1 for row in job_rows if _is_failure(row.status))
        runs = len(job_rows)
        rows_affected_total = sum(int(row.rows_affected or 0) for row in job_rows)

        summaries.append(
            CadenceJobDurationItem(
                job=job,
                runs=runs,
                failures=failures,
                failure_rate=failures / runs if runs else 0.0,
                min_seconds=min(durations) if durations else None,
                median_seconds=statistics.median(durations) if durations else None,
                p90_seconds=_percentile_90(durations),
                max_seconds=max(durations) if durations else None,
                rows_affected_total=rows_affected_total,
            )
        )

    return summaries


@router.get(
    "/cadence/jobs/duration",
    response_model=CadenceJobDurationResponse,
)
def get_cadence_job_durations(
    job: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> CadenceJobDurationResponse:
    statement = select(CadenceJobRun)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    rows = list(session.execute(statement).scalars())
    return CadenceJobDurationResponse(
        generated_at=datetime.now(timezone.utc),
        jobs=summarize_job_runs(rows),
    )


app = FastAPI(title="Cadence Job Duration Percentile API")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine)

    with TestingSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    started_at=datetime(2024, 1, 1, 0, 0, 0),
                    finished_at=datetime(2024, 1, 1, 0, 0, 10),
                    status="success",
                    rows_affected=3,
                ),
                CadenceJobRun(
                    job="alpha",
                    started_at=datetime(2024, 1, 1, 1, 0, 0),
                    finished_at=datetime(2024, 1, 1, 1, 0, 20),
                    status="failed",
                    rows_affected=2,
                ),
                CadenceJobRun(
                    job="beta",
                    started_at=datetime(2024, 1, 1, 2, 0, 0),
                    finished_at=datetime(2024, 1, 1, 2, 0, 30),
                    status="success",
                    rows_affected=4,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/cadence/jobs/duration")
    assert response.status_code == 200
    payload = response.json()
    assert {item["job"] for item in payload["jobs"]} == {"alpha", "beta"}
    alpha = next(item for item in payload["jobs"] if item["job"] == "alpha")
    assert alpha["runs"] == 2
    assert alpha["median_seconds"] == 15.0

    response = client.get("/api/cadence/jobs/duration", params={"job": "beta"})
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 1
    assert response.json()["jobs"][0]["job"] == "beta"

    print("PASS")