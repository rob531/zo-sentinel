"""Logic for the cadence job SLA breach feed."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


DEFAULT_SLA_SECONDS = 900
DEFAULT_WINDOW_RUNS = 50
_SUCCESS_STATUSES = {"ok", "success"}


def _as_datetime(value: Any) -> Optional[datetime]:
    """Convert supported timestamp values to a comparable datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
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


def _timestamp_text(value: Any) -> str:
    parsed = _as_datetime(value)
    if parsed is None:
        return ""
    return parsed.isoformat()


def _duration_seconds(started_at: Any, finished_at: Any) -> float:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return 0.0
    return round((finished - started).total_seconds(), 2)


def _run_is_successful(status: Any) -> bool:
    return str(status or "").strip().lower() in _SUCCESS_STATUSES


def compute_sla_breaches(
    db: Session,
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    window_runs: int = DEFAULT_WINDOW_RUNS,
) -> Dict[str, Any]:
    """Compute SLA status for the most recent runs of each cadence job.

    The trailing window is a bounded number of runs per job.  Ordering and
    limiting are performed in Python so the behavior is identical on SQLite
    and PostgreSQL and does not depend on database-specific date functions.
    """
    try:
        limit = int(window_runs)
    except (TypeError, ValueError):
        limit = DEFAULT_WINDOW_RUNS
    if limit < 1:
        limit = 1

    runs = list(
        db.execute(
            select(CadenceJobRun).order_by(
                CadenceJobRun.started_at.asc(),
                CadenceJobRun.id.asc(),
            )
        ).scalars()
    )

    by_job: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        by_job.setdefault(str(run.job), []).append(run)

    selected_runs: List[CadenceJobRun] = []
    for job_runs in by_job.values():
        selected_runs.extend(job_runs[-limit:])

    jobs: List[Dict[str, Any]] = []
    for job in sorted(by_job):
        job_runs = by_job[job]
        window = job_runs[-limit:]
        latest = window[-1]

        successful = sum(1 for run in window if _run_is_successful(run.status))
        success_rate = round(successful / len(window), 2) if window else 0.0
        duration = _duration_seconds(latest.started_at, latest.finished_at)

        jobs.append(
            {
                "job": job,
                "last_status": str(latest.status or ""),
                "last_finished_at": _timestamp_text(latest.finished_at),
                "last_duration_seconds": duration,
                "success_rate": success_rate,
                "breached": (
                    not _run_is_successful(latest.status)
                    or duration > float(sla_seconds)
                ),
            }
        )

    return {
        "window_runs": len(selected_runs),
        "jobs": jobs,
    }


def get_sla_breaches(
    db: Session,
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    window_runs: int = DEFAULT_WINDOW_RUNS,
) -> Dict[str, Any]:
    """Return the SLA breach payload for a request."""
    return compute_sla_breaches(
        db,
        sla_seconds=sla_seconds,
        window_runs=window_runs,
    )


def sla_breach_feed(
    db: Session,
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    window_runs: int = DEFAULT_WINDOW_RUNS,
) -> Dict[str, Any]:
    """Compatibility wrapper for callers using the feed naming convention."""
    return compute_sla_breaches(
        db,
        sla_seconds=sla_seconds,
        window_runs=window_runs,
    )


def get_sla_breach_feed(
    db: Session = Depends(get_session),
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    window_runs: int = DEFAULT_WINDOW_RUNS,
) -> Dict[str, Any]:
    """Dependency-injectable entry point for the SLA breach feed."""
    return compute_sla_breaches(
        db,
        sla_seconds=sla_seconds,
        window_runs=window_runs,
    )


def _self_test() -> None:
    """Exercise the computation against the real ORM model in isolation."""
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
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    testing_session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="scan",
                status="ok",
                started_at=now,
                finished_at=now,
                rows_affected=10,
                detail=None,
            ),
            CadenceJobRun(
                id=2,
                job="scan",
                status="ok",
                started_at=now,
                finished_at=now,
                rows_affected=20,
                detail=None,
            ),
            CadenceJobRun(
                id=3,
                job="scan",
                status="failed",
                started_at=now,
                finished_at=now,
                rows_affected=0,
                detail="failure",
            ),
            CadenceJobRun(
                id=4,
                job="threat_intel",
                status="ok",
                started_at=now,
                finished_at=now,
                rows_affected=5,
                detail=None,
            ),
        ]
    )
    testing_session.commit()

    local_app = FastAPI()

    @local_app.get("/api/cadence/sla-breaches")
    def endpoint(
        db: Session = Depends(get_session),
        sla_seconds: int = DEFAULT_SLA_SECONDS,
    ) -> Dict[str, Any]:
        return compute_sla_breaches(db, sla_seconds=sla_seconds)

    local_app.dependency_overrides[get_session] = lambda: testing_session
    try:
        response = TestClient(local_app).get(
            "/api/cadence/sla-breaches?sla_seconds=60"
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        scan = next(item for item in payload["jobs"] if item["job"] == "scan")
        threat = next(
            item for item in payload["jobs"] if item["job"] == "threat_intel"
        )
        assert scan["breached"] is True
        assert scan["success_rate"] == 0.67
        assert threat["breached"] is False
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()