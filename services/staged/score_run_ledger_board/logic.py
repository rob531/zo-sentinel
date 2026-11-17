"""logic.py -- score-run ledger board data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, FreshnessStat


def _iso_value(value: Any) -> Optional[str]:
    """Return a date/time value as an ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float)):
        text = str(value)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        return parsed.isoformat()
    return str(value)


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    """Calculate a wall-clock duration from two stored date/time values."""
    if started_at is None or finished_at is None:
        return None

    def parse(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    started = parse(started_at)
    finished = parse(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is not None and finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    elif finished.tzinfo is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (finished - started).total_seconds()


def _as_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_score_run_ledger_board(db: Session) -> Dict[str, Any]:
    """Build the score-run ledger board from cadence and freshness records."""
    run_rows = db.execute(
        select(
            CadenceJobRun.id,
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        )
    ).all()

    freshness_rows = db.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.compute_seconds,
            FreshnessStat.computed_at,
        )
    ).all()

    runs_by_job: Dict[str, List[Any]] = {}
    for row in run_rows:
        runs_by_job.setdefault(row.job or "", []).append(row)

    jobs: List[Dict[str, Any]] = []
    for job, rows in sorted(runs_by_job.items()):
        ok = sum(1 for row in rows if (row.status or "").lower() == "ok")
        failed = sum(1 for row in rows if (row.status or "").lower() == "failed")
        rows_affected_total = sum(
            int(row.rows_affected or 0)
            for row in rows
        )

        latest = max(
            rows,
            key=lambda row: (
                _sortable_date(row.started_at),
                int(row.id or 0),
            ),
        )

        jobs.append(
            {
                "job": job,
                "total_runs": len(rows),
                "ok": ok,
                "failed": failed,
                "rows_affected_total": rows_affected_total,
                "last_started_at": _iso_value(latest.started_at),
                "last_duration_seconds": _duration_seconds(
                    latest.started_at,
                    latest.finished_at,
                ),
            }
        )

    freshness_by_surface: Dict[str, List[Any]] = {}
    for row in freshness_rows:
        freshness_by_surface.setdefault(row.surface or "", []).append(row)

    surfaces: List[Dict[str, Any]] = []
    for surface, rows in sorted(freshness_by_surface.items()):
        durations = [
            value
            for value in (_as_number(row.compute_seconds) for row in rows)
            if value is not None
        ]
        average = sum(durations) / len(durations) if durations else None
        latest_computed = max(
            rows,
            key=lambda row: (_sortable_date(row.computed_at), row.surface or ""),
        )
        surfaces.append(
            {
                "surface": surface,
                "compute_seconds_avg": average,
                "computed_at": _iso_value(latest_computed.computed_at),
            }
        )

    return {
        "jobs": jobs,
        "totals": {
            "jobs": len(jobs),
            "runs": len(run_rows),
            "failed": sum(job["failed"] for job in jobs),
        },
        "surfaces": surfaces,
    }


def _sortable_date(value: Any) -> str:
    """Provide a stable ordering key for dates returned by either backend."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_score_run_ledger_board(db: Session) -> Dict[str, Any]:
    """Compatibility name for consumers that use the generic builder name."""
    return get_score_run_ledger_board(db)


def score_run_ledger_board(db: Session) -> Dict[str, Any]:
    """Compatibility name for the service-specific board entry point."""
    return get_score_run_ledger_board(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="daily-score",
                    status="ok",
                    started_at=datetime(2025, 1, 1, 0, 0, 0),
                    finished_at=datetime(2025, 1, 1, 0, 1, 0),
                    rows_affected=10,
                    detail="first",
                ),
                CadenceJobRun(
                    id=2,
                    job="daily-score",
                    status="ok",
                    started_at=datetime(2025, 1, 2, 0, 0, 0),
                    finished_at=datetime(2025, 1, 2, 0, 2, 0),
                    rows_affected=20,
                    detail="second",
                ),
                CadenceJobRun(
                    id=3,
                    job="nightly-score",
                    status="failed",
                    started_at=datetime(2025, 1, 3, 0, 0, 0),
                    finished_at=datetime(2025, 1, 3, 0, 0, 30),
                    rows_affected=0,
                    detail="failure",
                ),
            ]
        )
        session.add(
            FreshnessStat(
                surface="daily-score",
                payload={},
                compute_seconds=1.25,
                computed_at=datetime(2025, 1, 2, 0, 2, 0),
            )
        )
        session.commit()

        result = get_score_run_ledger_board(session)
        assert result["totals"]["runs"] == 3
        assert result["totals"]["failed"] == 1
        assert any(job["job"] == "nightly-score" for job in result["jobs"])
        assert result["surfaces"][0]["compute_seconds_avg"] == 1.25

    print("PASS")