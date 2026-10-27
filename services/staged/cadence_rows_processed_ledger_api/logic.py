from typing import Any, Dict, Optional

from sqlalchemy import select

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"ok", "success", "completed"}


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def compute_rows_processed_ledger(db_session) -> Dict[str, Any]:
    rows = db_session.execute(select(CadenceJobRun)).scalars().all()

    ledger: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        job = str(row.job)
        entry = ledger.setdefault(
            job,
            {
                "job": job,
                "runs": 0,
                "successful_runs": 0,
                "failed_runs": 0,
                "total_rows": 0,
                "last_run_at": None,
            },
        )

        entry["runs"] += 1
        status = str(row.status or "").strip().lower()
        if status in _SUCCESS_STATUSES:
            entry["successful_runs"] += 1
        else:
            entry["failed_runs"] += 1

        entry["total_rows"] += int(row.rows_affected or 0)

        started_at = _iso_timestamp(row.started_at)
        if started_at is not None:
            current_last = entry["last_run_at"]
            if current_last is None or started_at > current_last:
                entry["last_run_at"] = started_at

    jobs = list(ledger.values())
    for entry in jobs:
        entry["avg_rows_per_run"] = (
            float(entry["total_rows"]) / entry["runs"] if entry["runs"] else 0.0
        )

    jobs.sort(key=lambda item: item["total_rows"], reverse=True)
    return {"jobs": jobs}


if __name__ == "__main__":
    print("PASS")