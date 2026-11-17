from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from app.db import get_session
from app.models import CadenceJobRun

STALE_AFTER_SECONDS = 900
_FAILURE_STATUSES = {"failed", "failure", "error"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return _as_utc(datetime.fromisoformat(text))


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def compute_cadence_sla(rows: list[dict], now_iso: str) -> dict:
    now = _parse_datetime(now_iso)
    grouped: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        job = _row_value(row, "job")
        if job is None:
            continue
        grouped[str(job)].append(
            {
                "status": _row_value(row, "status"),
                "started_at": _row_value(row, "started_at"),
            }
        )

    jobs = []
    for job, job_rows in grouped.items():
        failures = sum(
            1
            for row in job_rows
            if str(row["status"] or "").strip().casefold() in _FAILURE_STATUSES
        )
        runs = len(job_rows)

        dated_rows = []
        for row in job_rows:
            started = row["started_at"]
            if started is None:
                continue
            if not isinstance(started, datetime):
                started = _parse_datetime(str(started))
            dated_rows.append((started, row))

        if dated_rows:
            last_started, last_row = max(
                dated_rows, key=lambda item: _as_utc(item[0])
            )
            last_started_at = _as_utc(last_started).isoformat()
            age_seconds = max(0, int((now - _as_utc(last_started)).total_seconds()))
        else:
            last_row = job_rows[-1]
            last_started_at = None
            age_seconds = 0

        stale = last_started_at is not None and age_seconds > STALE_AFTER_SECONDS
        failure_rate = failures / runs if runs else 0.0

        jobs.append(
            {
                "job": job,
                "runs": runs,
                "failures": failures,
                "failure_rate": failure_rate,
                "last_status": last_row["status"],
                "last_started_at": last_started_at,
                "age_seconds": age_seconds,
                "stale": stale,
            }
        )

    jobs.sort(key=lambda item: (-item["failure_rate"], item["job"]))
    return {
        "jobs": jobs,
        "worst_job": jobs[0]["job"] if jobs else None,
        "generated_at": now_iso,
    }


def get_cadence_sla(session=Depends(get_session)) -> dict:
    records = session.query(CadenceJobRun).all()
    rows = [
        {
            "job": record.job,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "rows_affected": record.rows_affected,
            "detail": record.detail,
        }
        for record in records
    ]
    now_iso = datetime.now(timezone.utc).isoformat()
    return compute_cadence_sla(rows, now_iso)


def get_cadence_sla_report(session=Depends(get_session)) -> dict:
    return get_cadence_sla(session=session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    Session = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with Session() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="healthy",
                    status="success",
                    started_at=now,
                    finished_at=now,
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    job="failing",
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    job="failing",
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    job="failing",
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    job="stale",
                    status="success",
                    started_at=now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=901),
                    finished_at=now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=901),
                    rows_affected=1,
                    detail={},
                ),
            ]
        )
        session.commit()

    def override_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/cadence/sla")
    def cadence_sla(session=Depends(get_session)):
        return get_cadence_sla(session=session)

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/sla")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 3
        by_job = {item["job"]: item for item in payload["jobs"]}
        assert by_job["failing"]["failure_rate"] == 1.0
        assert by_job["stale"]["stale"] is True

    print("PASS")