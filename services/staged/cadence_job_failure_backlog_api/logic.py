from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = ("ok", "success", "completed")


def _is_failure(status: Any) -> bool:
    """Return whether a run status represents a failure."""
    return status is None or str(status).strip().lower() not in _SUCCESS_STATUSES


def _failure_condition(model: Any):
    return or_(
        model.status.not_in(_SUCCESS_STATUSES),
        model.status.is_(None),
    )


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _query_failure_backlog(session: Session) -> dict[str, Any]:
    failed_run = aliased(CadenceJobRun)

    last_error_detail = (
        select(failed_run.detail)
        .where(
            failed_run.job == CadenceJobRun.job,
            _failure_condition(failed_run),
        )
        .order_by(
            failed_run.started_at.desc().nullslast(),
            failed_run.id.desc(),
        )
        .limit(1)
        .correlate(CadenceJobRun)
        .scalar_subquery()
    )

    statement = (
        select(
            CadenceJobRun.job.label("job"),
            func.count(CadenceJobRun.id).label("run_count"),
            func.sum(
                case((_failure_condition(CadenceJobRun), 1), else_=0)
            ).label("failure_count"),
            func.max(CadenceJobRun.started_at).label("last_started_at"),
            func.max(CadenceJobRun.finished_at).label("last_finished_at"),
            func.coalesce(
                func.sum(CadenceJobRun.rows_affected),
                0,
            ).label("rows_affected_sum"),
            last_error_detail.label("last_error_detail"),
        )
        .group_by(CadenceJobRun.job)
        .order_by(CadenceJobRun.job)
    )

    rows = session.execute(statement).mappings().all()

    jobs: list[dict[str, Any]] = []
    total_runs = 0
    total_failures = 0

    for row in rows:
        run_count = _as_int(row["run_count"])
        failure_count = _as_int(row["failure_count"])
        total_runs += run_count
        total_failures += failure_count

        jobs.append(
            {
                "job": row["job"],
                "status": "failure" if failure_count else "success",
                "run_count": run_count,
                "failure_count": failure_count,
                "last_started_at": _isoformat(row["last_started_at"]),
                "last_finished_at": _isoformat(row["last_finished_at"]),
                "rows_affected_sum": _as_int(row["rows_affected_sum"]),
                "last_error_detail": row["last_error_detail"],
            }
        )

    return {
        "jobs": jobs,
        "total_runs": total_runs,
        "total_failures": total_failures,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_failure_backlog(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return the cadence job failure backlog."""
    return _query_failure_backlog(session)


def build_failure_backlog(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Compatibility wrapper for callers using the build naming convention."""
    return _query_failure_backlog(session)


def list_failure_backlog(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Compatibility wrapper for callers using the list naming convention."""
    return _query_failure_backlog(session)


def failure_backlog(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Compatibility wrapper for callers using the endpoint naming convention."""
    return _query_failure_backlog(session)


__all__ = [
    "build_failure_backlog",
    "failure_backlog",
    "get_failure_backlog",
    "list_failure_backlog",
]


if __name__ == "__main__":
    assert callable(get_failure_backlog)
    print("PASS")