"""Logic layer for the cadence freshness SLO API."""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, FreshnessStat


_MAX_WINDOW_HOURS = 168
_SUCCESS_STATUSES = {"success", "succeeded", "successful", "ok", "completed"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_now() -> datetime:
    """Return a naive UTC value suitable for SQLite and PostgreSQL comparisons."""
    return _utc_now().replace(tzinfo=None)


def _timestamp(value: Optional[datetime]) -> Optional[str]:
    value = _as_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value else None


def _payload_value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            return None
        if isinstance(decoded, dict):
            return decoded.get(key)
    return None


def _payload_job(payload: Any, fallback: str) -> str:
    value = _payload_value(payload, "job")
    if value is None:
        value = _payload_value(payload, "surface")
    if value is None:
        value = fallback
    return str(value)


def _is_success(status: Any) -> bool:
    return str(status or "").strip().lower() in _SUCCESS_STATUSES


def _run_time(run: CadenceJobRun) -> Optional[datetime]:
    return _as_utc(run.started_at or run.finished_at)


def _success_time(run: CadenceJobRun) -> Optional[datetime]:
    if not _is_success(run.status):
        return None
    return _as_utc(run.finished_at or run.started_at)


def _median_interval_seconds(successful_times: Iterable[Optional[datetime]]) -> Optional[float]:
    times = sorted({time for time in successful_times if time is not None})
    if len(times) < 2:
        return None
    intervals = [
        (later - earlier).total_seconds()
        for earlier, later in zip(times, times[1:])
        if later >= earlier
    ]
    if not intervals:
        return None
    return float(statistics.median(intervals))


def get_freshness_slo(db: Session, hours: int = 24) -> Dict[str, Any]:
    """Compute the per-job cadence freshness and SLO summary for a time window."""
    try:
        requested_hours = int(hours)
    except (TypeError, ValueError):
        requested_hours = 24
    window_hours = max(1, min(requested_hours, _MAX_WINDOW_HOURS))
    now = _utc_now()
    cutoff = (now - timedelta(hours=window_hours)).replace(tzinfo=None)

    runs = list(
        db.execute(
            select(CadenceJobRun).where(
                or_(
                    CadenceJobRun.started_at.is_(None),
                    CadenceJobRun.started_at >= cutoff,
                )
            )
        ).scalars()
    )

    stats = list(db.execute(select(FreshnessStat)).scalars())

    jobs: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        jobs.setdefault(str(run.job), []).append(run)

    stats_by_job: Dict[str, List[float]] = {}
    for stat in stats:
        if stat.computed_at is not None:
            computed = _as_utc(stat.computed_at)
            if computed is not None and computed.replace(tzinfo=None) < cutoff:
                continue
        job = _payload_job(stat.payload, str(stat.surface))
        try:
            compute_seconds = float(stat.compute_seconds)
        except (TypeError, ValueError):
            compute_seconds = _payload_value(stat.payload, "compute_seconds")
            try:
                compute_seconds = float(compute_seconds)
            except (TypeError, ValueError):
                continue
        stats_by_job.setdefault(job, []).append(compute_seconds)

    for job in stats_by_job:
        jobs.setdefault(job, [])

    result_jobs: List[Dict[str, Any]] = []
    failing_jobs = 0
    delayed_jobs = 0

    for job in sorted(jobs):
        job_runs = jobs[job]
        runs_count = len(job_runs)
        failures = sum(1 for run in job_runs if not _is_success(run.status))
        failure_rate = failures / runs_count if runs_count else 0.0

        def latest_for(run: CadenceJobRun) -> Tuple[int, datetime]:
            run_time = _run_time(run)
            if run_time is None:
                return (0, datetime.min.replace(tzinfo=timezone.utc))
            return (1, run_time)

        latest_run = max(job_runs, key=latest_for) if job_runs else None
        successful_times = [_success_time(run) for run in job_runs]
        successful_times = [time for time in successful_times if time is not None]
        last_success = max(successful_times) if successful_times else None
        median_interval = _median_interval_seconds(successful_times)

        if failures:
            slo_status = "failing"
            failing_jobs += 1
        elif (
            last_success is not None
            and median_interval is not None
            and (now - last_success).total_seconds() > 4 * median_interval
        ):
            slo_status = "delayed"
            delayed_jobs += 1
        else:
            slo_status = "on_time"

        rows_affected = []
        for run in job_runs:
            if run.rows_affected is not None:
                try:
                    rows_affected.append(float(run.rows_affected))
                except (TypeError, ValueError):
                    pass

        compute_values = stats_by_job.get(job, [])
        result_jobs.append(
            {
                "job": job,
                "status": latest_run.status if latest_run is not None else None,
                "runs": runs_count,
                "failures": failures,
                "failure_rate": failure_rate,
                "last_started_at": _timestamp(latest_run.started_at if latest_run else None),
                "last_finished_at": _timestamp(latest_run.finished_at if latest_run else None),
                "last_success_at": _timestamp(last_success),
                "median_interval_seconds": median_interval,
                "mean_rows_affected": (
                    sum(rows_affected) / len(rows_affected) if rows_affected else 0.0
                ),
                "mean_compute_seconds": (
                    sum(compute_values) / len(compute_values) if compute_values else 0.0
                ),
                "slo_status": slo_status,
            }
        )

    return {
        "window_hours": window_hours,
        "generated_at": _timestamp(now),
        "jobs": result_jobs,
        "totals": {
            "jobs": len(result_jobs),
            "failing_jobs": failing_jobs,
            "delayed_jobs": delayed_jobs,
        },
    }


def compute_freshness_slo(db: Session, hours: int = 24) -> Dict[str, Any]:
    """Compatibility alias for callers that use the computation naming."""
    return get_freshness_slo(db, hours=hours)


def get_cadence_freshness_slo(db: Session, hours: int = 24) -> Dict[str, Any]:
    """Compatibility alias for the service's explicit cadence naming."""
    return get_freshness_slo(db, hours=hours)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    FreshnessStat.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = _db_now()
    with SessionLocal() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="healthy",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2, minutes=1),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=2,
                    job="healthy",
                    status="success",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4, minutes=1),
                    rows_affected=20,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=3,
                    job="flaky",
                    status="failed",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1, minutes=1),
                    rows_affected=0,
                    detail="failure",
                ),
                CadenceJobRun(
                    id=4,
                    job="flaky",
                    status="success",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3, minutes=1),
                    rows_affected=5,
                    detail="ok",
                ),
                FreshnessStat(
                    surface="healthy",
                    payload={"job": "healthy"},
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="flaky",
                    payload={"job": "flaky"},
                    compute_seconds=2.5,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/freshness-slo")
    def freshness_endpoint(hours: int = 24, db=Depends(get_session)):
        return get_freshness_slo(db, hours=hours)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/cadence/freshness-slo")
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["jobs"]) == 2
    by_job = {item["job"]: item for item in payload["jobs"]}
    assert by_job["flaky"]["slo_status"] == "failing"
    assert by_job["flaky"]["failure_rate"] > 0
    assert by_job["healthy"]["slo_status"] != "failing"
    print("PASS")