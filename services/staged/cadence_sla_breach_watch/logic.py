"""logic.py -- SLA breach computation for cadence job runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


SLA_DURATION_THRESHOLD_SECONDS = 300.0


def _is_success(status: str) -> bool:
    return status.strip().lower() in {"success", "succeeded", "ok", "completed"}


def _is_failure(status: str) -> bool:
    return status.strip().lower() in {"failed", "failure", "error", "errored"}


def _duration_seconds(
    started_at: Optional[datetime], finished_at: Optional[datetime]
) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None
    return max(0.0, (finished_at - started_at).total_seconds())


def get_sla_breaches(db: Session, window_hours: int = 24) -> Dict:
    """Compute per-job SLA statistics for runs started within the window."""
    hours = max(1, int(window_hours))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = db.scalars(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    ).all()

    grouped: Dict[str, List[CadenceJobRun]] = {}
    for run in rows:
        grouped.setdefault(run.job, []).append(run)

    jobs: List[Dict] = []
    for job, runs in sorted(grouped.items()):
        succeeded = sum(1 for run in runs if _is_success(run.status or ""))
        failed = sum(1 for run in runs if _is_failure(run.status or ""))
        durations = [
            duration
            for run in runs
            if (
                duration := _duration_seconds(run.started_at, run.finished_at)
            )
            is not None
        ]
        mean_seconds = sum(durations) / len(durations) if durations else None
        if durations:
            ordered = sorted(durations)
            rank = max(0, min(len(ordered) - 1, int((len(ordered) * 95 + 99) // 100) - 1))
            p95_seconds = ordered[rank]
        else:
            p95_seconds = None

        successful_runs = [
            run for run in runs if _is_success(run.status or "")
        ]
        last_success_at = None
        if successful_runs:
            last_success_at = max(
                (
                    run.finished_at or run.started_at
                    for run in successful_runs
                ),
                default=None,
            )

        breach = succeeded == 0 or (
            mean_seconds is not None
            and mean_seconds > SLA_DURATION_THRESHOLD_SECONDS
        )

        jobs.append(
            {
                "job": job,
                "total": len(runs),
                "succeeded": succeeded,
                "failed": failed,
                "mean_seconds": (
                    round(mean_seconds, 3) if mean_seconds is not None else None
                ),
                "p95_seconds": (
                    round(p95_seconds, 3) if p95_seconds is not None else None
                ),
                "last_success_at": last_success_at,
                "breach": bool(breach),
            }
        )

    return {
        "window_hours": hours,
        "jobs": jobs,
        "breaches": sum(1 for job in jobs if job["breach"]),
    }


def get_cadence_sla_breaches(
    window_hours: int = 24, db: Session = Depends(get_session)
) -> Dict:
    """FastAPI dependency wrapper for the SLA breach computation."""
    return get_sla_breaches(db, window_hours)


compute_sla_breaches = get_sla_breaches


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
    session_factory = sessionmaker(bind=engine)
    test_session = session_factory()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.add_all(
        [
            CadenceJobRun(
                job="healthy_job",
                status="success",
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                rows_affected=10,
                detail="ok",
            ),
            CadenceJobRun(
                job="healthy_job",
                status="success",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                rows_affected=12,
                detail="ok",
            ),
            CadenceJobRun(
                job="failed_job",
                status="failed",
                started_at=now - timedelta(minutes=3),
                finished_at=now - timedelta(minutes=2),
                rows_affected=0,
                detail="failed",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/sla-breaches")
    def cadence_sla_breaches(
        window_hours: int = 24,
        db: Session = Depends(get_session),
    ):
        return get_cadence_sla_breaches(window_hours, db)

    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get(
        "/api/cadence/sla-breaches", params={"window_hours": 24}
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    jobs = {item["job"]: item for item in payload["jobs"]}
    assert jobs["failed_job"]["breach"] is True
    assert jobs["healthy_job"]["breach"] is False
    assert jobs["healthy_job"]["succeeded"] == 2
    assert jobs["failed_job"]["succeeded"] == 0
    print("PASS")