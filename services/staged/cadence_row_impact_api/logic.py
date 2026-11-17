from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _parse_detail(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _as_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _detail_rows_affected(detail: Mapping[str, Any]) -> int | None:
    for key in ("rows_affected", "rowsAffected", "row_count", "rowCount"):
        if key in detail:
            value = detail[key]
            if isinstance(value, bool):
                return int(value)
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return None
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return None
    try:
        return (finished - started).total_seconds()
    except TypeError:
        return None


def summarize_cadence_runs(runs: list[CadenceJobRun]) -> dict[str, Any]:
    grouped: dict[str, list[CadenceJobRun]] = defaultdict(list)
    for run in runs:
        grouped[str(run.job)].append(run)

    jobs: list[dict[str, Any]] = []
    for job in sorted(grouped):
        job_runs = grouped[job]
        status_counts: dict[str, int] = {}
        succeeded = 0
        failed = 0
        total_rows_affected = 0
        successful_rows: list[int] = []
        durations: list[float] = []
        last_started_at: datetime | None = None

        for run in job_runs:
            raw_status = str(run.status).strip() if run.status is not None else "unknown"
            status_counts[raw_status] = status_counts.get(raw_status, 0) + 1

            normalized_status = raw_status.lower()
            if normalized_status == "succeeded":
                succeeded += 1
                detail = _parse_detail(run.detail)
                rows_affected = (
                    _as_int(run.rows_affected)
                    if run.rows_affected is not None
                    else _detail_rows_affected(detail)
                )
                rows_affected = rows_affected if rows_affected is not None else 0
                total_rows_affected += rows_affected
                successful_rows.append(rows_affected)
            elif normalized_status == "failed":
                failed += 1

            started_at = _as_datetime(run.started_at)
            if started_at is not None and (
                last_started_at is None or started_at > last_started_at
            ):
                last_started_at = started_at

            duration = _duration_seconds(run.started_at, run.finished_at)
            if duration is not None:
                durations.append(duration)

        jobs.append(
            {
                "job": job,
                "runs": len(job_runs),
                "succeeded": succeeded,
                "failed": failed,
                "total_rows_affected": total_rows_affected,
                "avg_rows_affected": (
                    sum(successful_rows) / len(successful_rows)
                    if successful_rows
                    else 0.0
                ),
                "last_started_at": last_started_at,
                "avg_duration_seconds": (
                    sum(durations) / len(durations) if durations else 0.0
                ),
                "status_counts": status_counts,
            }
        )

    return {
        "jobs": jobs,
        "totals": {"jobs": len(jobs), "runs": len(runs)},
    }


def get_cadence_row_impact(
    db: Session = Depends(get_session),
    job: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if (db is None or isinstance(db, str)) and isinstance(job, Session):
        db, job = job, db

    session = kwargs.pop("session", None)
    if session is None:
        session = db
    if not isinstance(session, Session):
        raise TypeError("A database session is required")

    statement = select(CadenceJobRun).order_by(
        CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc()
    )
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    runs = list(session.execute(statement).scalars().all())
    return summarize_cadence_runs(runs)


get_cadence_row_impact_summary = get_cadence_row_impact
summarize_cadence_row_impact = get_cadence_row_impact


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()

    @test_app.get("/api/cadence/row-impact")
    def cadence_row_impact_endpoint(
        job: str | None = None,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_cadence_row_impact(session, job)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with test_session_factory() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="succeeded",
                    started_at=datetime(2025, 1, 1, 10, 0, 0),
                    finished_at=datetime(2025, 1, 1, 10, 0, 5),
                    rows_affected=7,
                    detail={"source": "test"},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=datetime(2025, 1, 2, 10, 0, 0),
                    finished_at=datetime(2025, 1, 2, 10, 0, 2),
                    rows_affected=100,
                    detail="invalid-json",
                ),
                CadenceJobRun(
                    job="beta",
                    status="running",
                    started_at=datetime(2025, 1, 3, 10, 0, 0),
                    finished_at=None,
                    rows_affected=0,
                    detail=None,
                ),
            ]
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/row-impact")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        assert sum(job["total_rows_affected"] for job in payload["jobs"]) == 7
        alpha = next(job for job in payload["jobs"] if job["job"] == "alpha")
        assert alpha["status_counts"]["failed"] == 1

    print("PASS")