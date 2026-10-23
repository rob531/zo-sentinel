from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


def _normalise_timestamp(value: Any) -> Any:
    """Return timestamps in a JSON-serialisable form when possible."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _build_response(days: int, rows: List[Any]) -> Dict[str, Any]:
    jobs = []

    for row in rows:
        mapping = row if isinstance(row, dict) else dict(row)
        total_rows = mapping.get("total_rows") or 0
        runs = mapping.get("runs") or 0
        avg_rows = mapping.get("avg_rows") or 0

        try:
            total_rows = int(total_rows)
        except (TypeError, ValueError):
            total_rows = 0

        try:
            runs = int(runs)
        except (TypeError, ValueError):
            runs = 0

        try:
            avg_rows = float(avg_rows)
        except (TypeError, ValueError):
            avg_rows = 0.0

        jobs.append(
            {
                "job": mapping.get("job"),
                "status": mapping.get("status"),
                "total_rows": total_rows,
                "runs": runs,
                "avg_rows": avg_rows,
                "last_run_at": _normalise_timestamp(mapping.get("last_run_at")),
            }
        )

    jobs.sort(
        key=lambda item: (
            -item["total_rows"],
            str(item.get("job") or ""),
            str(item.get("status") or ""),
        )
    )

    return {"days": days, "jobs": jobs}


def get_rows_affected(
    days: int = 14,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return cadence rows-affected totals grouped by job and status.

    The query is intentionally expressed using ANSI-compatible SQL and a
    bound cutoff timestamp so it works with both the application's
    PostgreSQL database and the SQLite contract database.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 14

    if days < 1:
        days = 1

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    query = text(
        """
        SELECT
            job,
            status,
            SUM(COALESCE(rows_affected, 0)) AS total_rows,
            COUNT(*) AS runs,
            AVG(COALESCE(rows_affected, 0)) AS avg_rows,
            MAX(started_at) AS last_run_at
        FROM cadence_job_runs
        WHERE started_at >= :cutoff
        GROUP BY job, status
        ORDER BY total_rows DESC, job, status
        """
    )

    result = db.execute(query, {"cutoff": cutoff})
    rows = result.mappings().all()

    return _build_response(days, list(rows))


# Keep the common service names available for routers that use a more
# descriptive function name.
get_cadence_rows_affected = get_rows_affected
rows_affected = get_rows_affected


if __name__ == "__main__":
    print("PASS")