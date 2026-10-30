from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from statistics import median


_DURATION_STATUSES = {
    "ok",
    "success",
    "succeeded",
    "complete",
    "completed",
}
_FAILURE_STATUSES = {
    "failed",
    "failure",
    "error",
    "errored",
}


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _duration_seconds(run):
    started_at = _parse_datetime(run.get("started_at"))
    finished_at = _parse_datetime(run.get("finished_at"))
    if started_at is None or finished_at is None:
        return None

    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    else:
        finished_at = finished_at.astimezone(timezone.utc)
        started_at = started_at.astimezone(timezone.utc)

    try:
        return (finished_at - started_at).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None


def _percentile_nearest_rank(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


def compute_profile(runs: list[dict]) -> dict:
    grouped = {}
    for run in runs or []:
        job = str(run.get("job", ""))
        if job not in grouped:
            grouped[job] = {
                "runs": 0,
                "ok": 0,
                "failed": 0,
                "durations": [],
                "total_rows_affected": 0,
            }

        entry = grouped[job]
        entry["runs"] += 1

        status = str(run.get("status", "")).strip().lower()
        if status in _DURATION_STATUSES:
            entry["ok"] += 1
        elif status in _FAILURE_STATUSES:
            entry["failed"] += 1

        duration = _duration_seconds(run)
        if duration is not None:
            entry["durations"].append(float(duration))

        rows_affected = run.get("rows_affected")
        if rows_affected is not None:
            try:
                entry["total_rows_affected"] += int(rows_affected)
            except (TypeError, ValueError):
                pass

    jobs = []
    all_durations = []
    for job in sorted(grouped):
        entry = grouped[job]
        durations = entry["durations"]
        all_durations.extend(durations)
        jobs.append(
            {
                "job": job,
                "runs": entry["runs"],
                "ok": entry["ok"],
                "failed": entry["failed"],
                "median_seconds": float(median(durations)) if durations else 0.0,
                "p95_seconds": _percentile_nearest_rank(durations, 0.95),
                "max_seconds": float(max(durations)) if durations else 0.0,
                "total_rows_affected": entry["total_rows_affected"],
            }
        )

    return {
        "jobs": jobs,
        "totals": {
            "jobs": len(jobs),
            "runs": sum(job["runs"] for job in jobs),
            "ok": sum(job["ok"] for job in jobs),
            "failed": sum(job["failed"] for job in jobs),
            "median_seconds": float(median(all_durations)) if all_durations else 0.0,
            "p95_seconds": _percentile_nearest_rank(all_durations, 0.95),
        },
    }


def _quote_sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def _fetch_from_session(session, sql):
    connection = session.connection()
    if not hasattr(connection, "exec_driver_sql"):
        raise TypeError(
            "The supplied SQLAlchemy-style session does not expose "
            "connection().exec_driver_sql()."
        )
    result = connection.exec_driver_sql(sql)
    rows = result.mappings().all()
    return [dict(row) for row in rows]


def _fetch_from_dbapi(connection, sql):
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        description = getattr(cursor, "description", None) or []
        columns = [column[0] for column in description]
        rows = cursor.fetchall()
    finally:
        close = getattr(cursor, "close", None)
        if close is not None:
            close()

    result = []
    for row in rows:
        if isinstance(row, dict):
            result.append(dict(row))
        elif columns:
            result.append(dict(zip(columns, row)))
        else:
            result.append(dict(row))
    return result


def fetch_runs(session_or_conn, job: str | None = None, limit: int = 5000) -> list[dict]:
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 0:
        raise ValueError("limit must be non-negative")

    query = (
        "SELECT id, job, status, started_at, finished_at, rows_affected, detail "
        "FROM cadence_job_runs"
    )
    if job is not None:
        query += " WHERE job = " + _quote_sql_string(job)
    query += " ORDER BY started_at DESC LIMIT " + str(limit)

    if hasattr(session_or_conn, "connection") and hasattr(
        session_or_conn.connection(), "exec_driver_sql"
    ):
        return _fetch_from_session(session_or_conn, query)
    if hasattr(session_or_conn, "cursor"):
        return _fetch_from_dbapi(session_or_conn, query)
    raise TypeError(
        "session_or_conn must be a SQLAlchemy-style session or a DB-API connection"
    )


def render_text(profile: dict) -> str:
    lines = []
    for job in sorted(profile.get("jobs", []), key=lambda item: item.get("job", "")):
        lines.append(
            "{job}\truns={runs}\tok={ok}\tfailed={failed}\t"
            "median_seconds={median_seconds}\tp95_seconds={p95_seconds}\t"
            "max_seconds={max_seconds}\ttotal_rows_affected={total_rows_affected}".format(
                job=job.get("job", ""),
                runs=job.get("runs", 0),
                ok=job.get("ok", 0),
                failed=job.get("failed", 0),
                median_seconds=job.get("median_seconds", 0.0),
                p95_seconds=job.get("p95_seconds", 0.0),
                max_seconds=job.get("max_seconds", 0.0),
                total_rows_affected=job.get("total_rows_affected", 0),
            )
        )
    return "\n".join(lines)


def render_json(profile: dict) -> str:
    return json.dumps(profile, indent=2, sort_keys=True, default=str)


def run() -> dict:
    try:
        from app.db import get_session
    except Exception as exc:
        raise RuntimeError(
            "Unable to import get_session from app.db; the database session "
            "helper is required."
        ) from exc

    session = get_session()
    entered = None
    try:
        if hasattr(session, "__enter__") and hasattr(session, "__exit__"):
            entered = session
            session = entered.__enter__()
        runs = fetch_runs(session)
    except Exception:
        if entered is not None:
            entered.__exit__(*__import__("sys").exc_info())
        else:
            close = getattr(session, "close", None)
            if close is not None:
                close()
        raise
    else:
        if entered is not None:
            entered.__exit__(None, None, None)
        else:
            close = getattr(session, "close", None)
            if close is not None:
                close()

    profile = compute_profile(runs)
    print(render_text(profile))
    print(render_json(profile))
    return profile


if __name__ == "__main__":
    from datetime import datetime, timedelta

    started = datetime(2024, 1, 1, 12, 0, 0)
    synthetic_runs = [
        {
            "job": "scanner",
            "status": "ok",
            "started_at": started,
            "finished_at": started + timedelta(seconds=10),
            "rows_affected": 2,
            "detail": {},
        },
        {
            "job": "scanner",
            "status": "ok",
            "started_at": started + timedelta(seconds=30),
            "finished_at": started + timedelta(seconds=50),
            "rows_affected": 3,
            "detail": {},
        },
        {
            "job": "scanner",
            "status": "failed",
            "started_at": started + timedelta(seconds=60),
            "finished_at": None,
            "rows_affected": None,
            "detail": {},
        },
        {
            "job": "advisory_ingest",
            "status": "ok",
            "started_at": started,
            "finished_at": None,
            "rows_affected": 1,
            "detail": {},
        },
    ]

    profile = compute_profile(synthetic_runs)
    assert profile["totals"]["runs"] == 4
    assert profile["totals"]["jobs"] == 2

    scanner = next(job for job in profile["jobs"] if job["job"] == "scanner")
    assert scanner["ok"] == 2
    assert scanner["failed"] == 1
    assert scanner["median_seconds"] == 15.0
    assert scanner["max_seconds"] == 20.0

    advisory = next(
        job for job in profile["jobs"] if job["job"] == "advisory_ingest"
    )
    assert advisory["median_seconds"] == 0.0

    assert json.loads(render_json(profile)) == profile
    print("PASS")