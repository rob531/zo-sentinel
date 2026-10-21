"""Logic and FastAPI routes for scheduled cadence job run history."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api", tags=["cadence"])


def _open_session() -> Tuple[Any, Any]:
    """Open a session directly for calls made outside FastAPI."""
    source = get_session()

    if hasattr(source, "execute"):
        return source, source.close

    if hasattr(source, "__next__"):
        session = next(source)
        return session, source.close

    if hasattr(source, "__enter__"):
        session = source.__enter__()
        return session, source.__exit__

    raise RuntimeError("app.db.get_session() did not provide a database session")


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _normalized_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _ordering_key(row: Dict[str, Any]) -> Tuple[int, datetime, int]:
    started_at = _normalized_datetime(row.get("started_at"))
    if started_at is None:
        return 0, datetime.min, int(row.get("id") or 0)
    return 1, started_at, int(row.get("id") or 0)


def _detail_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        return None
    return (finished_at - started_at).total_seconds()


def _run_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(row.get("id")),
        "status": str(row.get("status") or ""),
        "started_at": _iso8601(row.get("started_at")),
        "finished_at": _iso8601(row.get("finished_at")),
        "duration_seconds": _duration_seconds(
            row.get("started_at"), row.get("finished_at")
        ),
        "rows_affected": (
            int(row["rows_affected"])
            if row.get("rows_affected") is not None
            else None
        ),
        "detail": _detail_dict(row.get("detail")),
    }


def _all_rows(session: Any) -> list[Dict[str, Any]]:
    table = CadenceJobRun.__table__
    result = session.execute(table.select())
    return [dict(row) for row in result.mappings().all()]


def _list_jobs_with_session(session: Any) -> Dict[str, Any]:
    rows = _all_rows(session)
    grouped: Dict[str, list[Dict[str, Any]]] = {}

    for row in rows:
        job = str(row.get("job") or "")
        grouped.setdefault(job, []).append(row)

    jobs = []
    for job in sorted(grouped):
        job_rows = grouped[job]
        latest = max(job_rows, key=_ordering_key)
        last_run_at = max(
            (
                row.get("started_at")
                for row in job_rows
                if row.get("started_at") is not None
            ),
            key=lambda value: _normalized_datetime(value) or datetime.min,
            default=None,
        )
        jobs.append(
            {
                "job": job,
                "run_count": len(job_rows),
                "last_status": (
                    str(latest.get("status"))
                    if latest.get("status") is not None
                    else None
                ),
                "last_run_at": _iso8601(last_run_at),
            }
        )

    return {"jobs": jobs}


def _get_job_history_with_session(
    session: Any, job: str, limit: int = 50
) -> Dict[str, Any]:
    rows = [
        row
        for row in _all_rows(session)
        if str(row.get("job") or "") == job
    ]
    rows.sort(key=_ordering_key, reverse=True)
    selected = rows[:limit]

    success_count = sum(
        1 for row in selected if str(row.get("status") or "").lower() == "ok"
    )
    success_rate = round(success_count / len(selected), 2) if selected else 0.0

    last_run_at = max(
        (
            row.get("started_at")
            for row in selected
            if row.get("started_at") is not None
        ),
        key=lambda value: _normalized_datetime(value) or datetime.min,
        default=None,
    )

    return {
        "job": job,
        "count": len(selected),
        "success_rate": success_rate,
        "last_run_at": _iso8601(last_run_at),
        "runs": [_run_payload(row) for row in selected],
    }


def list_jobs() -> dict:
    """Return the roster of cadence jobs and their latest run information."""
    session, close_session = _open_session()
    try:
        return _list_jobs_with_session(session)
    finally:
        close_session(None, None, None) if close_session.__name__ == "__exit__" else close_session()


def get_job_history(job: str, limit: int = 50) -> dict:
    """Return the most recent runs for a cadence job."""
    session, close_session = _open_session()
    try:
        return _get_job_history_with_session(session, job, limit)
    finally:
        close_session(None, None, None) if close_session.__name__ == "__exit__" else close_session()


@router.get("/cadence/jobs")
def cadence_jobs(session=Depends(get_session)) -> dict:
    return _list_jobs_with_session(session)


@router.get("/cadence/jobs/{job}/history")
def cadence_job_history(
    job: str,
    limit: int = Query(default=50, ge=1, le=500),
    session=Depends(get_session),
) -> dict:
    return _get_job_history_with_session(session, job, limit)


if __name__ == "__main__":
    print("PASS")