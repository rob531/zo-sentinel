from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"success", "succeeded", "completed", "complete", "ok"}
_FAILED_STATUSES = {"failed", "failure", "error"}


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        return None
    duration = (finished_at - started_at).total_seconds()
    return max(0.0, duration)


def get_stats(
    days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if days < 1 or days > 90:
        raise HTTPException(status_code=422, detail="days must be between 1 and 90")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = cutoff.replace(microsecond=0)
    cutoff = cutoff.fromtimestamp(cutoff.timestamp() - (days * 24 * 60 * 60), tz=timezone.utc).replace(
        tzinfo=None
    )

    statement = (
        select(
            CadenceJobRun.id,
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        )
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc())
    )
    rows = session.execute(statement).all()

    grouped: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        grouped[str(row.job)].append(row)

    jobs: List[Dict[str, Any]] = []
    total_runs = 0
    total_failed = 0

    for job_name, job_rows in grouped.items():
        runs = len(job_rows)
        succeeded = 0
        failed = 0
        rows_affected_total = 0
        durations: List[float] = []
        last_started_at: Any = None

        for row in job_rows:
            status = str(row.status or "").strip().lower()
            if status in _SUCCESS_STATUSES:
                succeeded += 1
            elif status in _FAILED_STATUSES:
                failed += 1

            if row.rows_affected is not None:
                rows_affected_total += int(row.rows_affected)

            duration = _duration_seconds(row.started_at, row.finished_at)
            if duration is not None:
                durations.append(duration)

            if last_started_at is None:
                last_started_at = row.started_at
            elif isinstance(last_started_at, datetime) and isinstance(row.started_at, datetime):
                if row.started_at > last_started_at:
                    last_started_at = row.started_at
            elif row.started_at is not None:
                last_started_at = row.started_at

        total_runs += runs
        total_failed += failed

        jobs.append(
            {
                "job": job_name,
                "runs": runs,
                "succeeded": succeeded,
                "failed": failed,
                "success_rate": succeeded / runs if runs else 0.0,
                "rows_affected_total": rows_affected_total,
                "avg_duration_seconds": (
                    sum(durations) / len(durations) if durations else None
                ),
                "last_run_at": _iso_timestamp(last_started_at),
            }
        )

    jobs.sort(key=lambda item: (-item["runs"], item["job"]))

    return {
        "days": days,
        "jobs": jobs,
        "totals": {
            "runs": total_runs,
            "failed": total_failed,
        },
    }


def get_throughput(
    days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_stats(days=days, session=session)


def get_cadence_throughput(
    days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_stats(days=days, session=session)


def _self_test() -> None:
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
    testing_session = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    seed_rows = [
        CadenceJobRun(
            job="alpha",
            status="succeeded",
            started_at=now,
            finished_at=now,
            rows_affected=10,
            detail=None,
        ),
        CadenceJobRun(
            job="alpha",
            status="failed",
            started_at=now,
            finished_at=now,
            rows_affected=0,
            detail=None,
        ),
        CadenceJobRun(
            job="alpha",
            status="succeeded",
            started_at=now,
            finished_at=None,
            rows_affected=5,
            detail=None,
        ),
        CadenceJobRun(
            job="beta",
            status="succeeded",
            started_at=now,
            finished_at=now,
            rows_affected=20,
            detail=None,
        ),
        CadenceJobRun(
            job="beta",
            status="failed",
            started_at=now,
            finished_at=now,
            rows_affected=0,
            detail=None,
        ),
        CadenceJobRun(
            job="beta",
            status="failed",
            started_at=now,
            finished_at=None,
            rows_affected=0,
            detail=None,
        ),
    ]

    with testing_session() as db:
        db.add_all(seed_rows)
        db.commit()

    def override_get_session():
        with testing_session() as db:
            yield db

    application = FastAPI()

    @application.get("/api/cadence/throughput")
    def throughput(
        days: int = Query(7, ge=1, le=90),
        session: Session = Depends(get_session),
    ):
        return get_stats(days=days, session=session)

    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/cadence/throughput", params={"days": 7})
        assert response.status_code == 200
        payload = response.json()

        assert payload["totals"]["runs"] == 6
        assert payload["totals"]["failed"] == 3
        assert {job["job"]: job["runs"] for job in payload["jobs"]} == {
            "alpha": 3,
            "beta": 3,
        }
        assert all(0.0 <= job["success_rate"] <= 1.0 for job in payload["jobs"])
        assert all(
            job["avg_duration_seconds"] is None or job["avg_duration_seconds"] >= 0
            for job in payload["jobs"]
        )
        assert payload["jobs"][0]["runs"] >= payload["jobs"][1]["runs"]

    print("PASS")


if __name__ == "__main__":
    _self_test()