from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float:
    return (_as_utc(finished_at) - _as_utc(started_at)).total_seconds()


def _aggregate_rows(rows: list[CadenceJobRun]) -> list[dict[str, Any]]:
    grouped: dict[str, list[CadenceJobRun]] = {}

    for row in rows:
        grouped.setdefault(row.job, []).append(row)

    result: list[dict[str, Any]] = []
    for job in sorted(grouped):
        job_rows = grouped[job]
        completed = [
            row
            for row in job_rows
            if row.started_at is not None and row.finished_at is not None
        ]
        row_values = [
            row.rows_affected
            for row in job_rows
            if row.rows_affected is not None
        ]
        finished_values = [
            row.finished_at
            for row in job_rows
            if row.finished_at is not None
        ]

        result.append(
            {
                "job": job,
                "runs": len(job_rows),
                "failed_runs": sum(1 for row in job_rows if row.status == "failed"),
                "rows_total": sum(row_values) if row_values else 0,
                "rows_max": max(row_values) if row_values else None,
                "mean_duration_s": (
                    sum(
                        _duration_seconds(row.started_at, row.finished_at)
                        for row in completed
                    )
                    / len(completed)
                    if completed
                    else None
                ),
                "last_finished_at": (
                    max(finished_values, key=_as_utc) if finished_values else None
                ),
            }
        )

    return result


def aggregate_cadence_rows_processed(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    statement = (
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    )
    rows = list(session.execute(statement).scalars().all())

    return {
        "days": days,
        "jobs": _aggregate_rows(rows),
    }


def get_cadence_rows_processed(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if not isinstance(days, (int, float)) and isinstance(session, int):
        days, session = session, days
    return aggregate_cadence_rows_processed(days=days, session=session)


def get_rows_processed(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_cadence_rows_processed(days=days, session=session)


def cadence_rows_processed(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_cadence_rows_processed(days=days, session=session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as session:
            yield session

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2, minutes=5),
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3, minutes=2),
                    rows_affected=None,
                    detail={},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4, minutes=3),
                    rows_affected=5,
                    detail={},
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(hours=5),
                    finished_at=now - timedelta(hours=5, minutes=4),
                    rows_affected=7,
                    detail={},
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(hours=6),
                    finished_at=now - timedelta(hours=6, minutes=2),
                    rows_affected=2,
                    detail={},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/rows-processed")
    def rows_processed_endpoint(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_cadence_rows_processed(days=days, session=session)

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/rows-processed", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        jobs = {job["job"]: job for job in payload["jobs"]}
        assert jobs["alpha"]["runs"] == 3
        assert jobs["beta"]["runs"] == 2
        assert jobs["alpha"]["failed_runs"] == 1
        assert jobs["alpha"]["rows_total"] == 15
        assert jobs["beta"]["rows_total"] == 9

    print("PASS")