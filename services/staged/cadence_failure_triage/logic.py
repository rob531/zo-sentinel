"""logic.py -- cadence failure triage data/computation layer.

Reads the real ``cadence_job_runs`` table through the application SQLAlchemy
session and performs the filtering and aggregation for the cadence failures
endpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_FAILURE_STATUSES = ("failed", "error")
_ERROR_KEYS = ("error", "exception", "message")


def _error_hint(detail: Any) -> Optional[str]:
    """Extract a useful, bounded error message from a JSON detail value."""
    if detail is None:
        return None

    if isinstance(detail, dict):
        for key in _ERROR_KEYS:
            value = detail.get(key)
            if isinstance(value, str) and value:
                return value[:200]
        for value in detail.values():
            if isinstance(value, str) and value:
                return value[:200]
        return None

    if isinstance(detail, (list, tuple)):
        for value in detail:
            if isinstance(value, str) and value:
                return value[:200]
        return None

    if isinstance(detail, str):
        return detail[:200] or None

    return None


def _as_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        return None

    duration = (finished_at - started_at).total_seconds()
    return max(0.0, duration)


def get_cadence_failures(db: Session, days: int = 7) -> Dict[str, Any]:
    """Return failed and errored cadence runs from the requested time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = (
        select(CadenceJobRun)
        .where(
            func.lower(CadenceJobRun.status).in_(_FAILURE_STATUSES),
            CadenceJobRun.started_at >= cutoff,
        )
        .order_by(CadenceJobRun.started_at.desc())
    )
    runs = db.execute(statement).scalars().all()

    failures = []
    job_stats: Dict[str, Dict[str, float]] = {}
    total_duration = 0.0

    for run in runs:
        duration = _duration_seconds(run.started_at, run.finished_at)
        if duration is not None:
            total_duration += duration

        job_stats.setdefault(run.job, {"count": 0, "duration": 0.0})
        job_stats[run.job]["count"] += 1
        if duration is not None:
            job_stats[run.job]["duration"] += duration

        failures.append(
            {
                "id": run.id,
                "job": run.job,
                "status": run.status,
                "started_at": _as_iso(run.started_at),
                "finished_at": _as_iso(run.finished_at),
                "duration_seconds": duration,
                "rows_affected": run.rows_affected,
                "error_hint": _error_hint(run.detail),
            }
        )

    worst_job = None
    if job_stats:
        worst_job = sorted(
            job_stats.items(),
            key=lambda item: (-item[1]["count"], -item[1]["duration"], item[0]),
        )[0][0]

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        "summary": {
            "failed_runs": len(failures),
            "distinct_jobs": len(job_stats),
            "worst_job": worst_job,
            "total_duration_seconds": total_duration,
        },
    }


# Names used by thin routers and compatibility callers.
get_failures = get_cadence_failures
get_failure_triage = get_cadence_failures
get_cadence_failure_triage = get_cadence_failures


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
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
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/failures")
    def cadence_failures(
        days: int = Query(default=7, ge=1, le=90),
        db: Session = Depends(get_session),
    ):
        return get_cadence_failures(db, days)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="seeded_job",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1),
                    rows_affected=10,
                    detail={"error": "seeded error message"},
                ),
                CadenceJobRun(
                    id=2,
                    job="seeded_job",
                    status="failed",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=3),
                    rows_affected=20,
                    detail={"message": "another seeded error"},
                ),
                CadenceJobRun(
                    id=3,
                    job="seeded_job",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1),
                    rows_affected=30,
                    detail={},
                ),
                CadenceJobRun(
                    id=4,
                    job="seeded_job",
                    status="running",
                    started_at=now - timedelta(minutes=30),
                    finished_at=None,
                    rows_affected=None,
                    detail={},
                ),
            ]
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/failures?days=7")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["failed_runs"] == 2
        assert payload["summary"]["distinct_jobs"] == 1
        assert any(
            failure.get("error_hint") == "seeded error message"
            for failure in payload["failures"]
        )
        assert all(failure.get("status") != "success" for failure in payload["failures"])

    print("PASS")