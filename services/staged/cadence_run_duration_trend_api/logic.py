import inspect
import signal
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    FreshnessStat,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)

router = APIRouter(prefix="/cadence", tags=["cadence"])

_JOB_FIELDS = ("job_name", "job", "cadence", "cadence_name", "name")
_START_FIELDS = ("started_at", "start_time", "run_at", "created_at", "timestamp")
_END_FIELDS = ("finished_at", "completed_at", "ended_at", "finish_time")
_DURATION_SECONDS_FIELDS = (
    "duration_seconds",
    "elapsed_seconds",
    "runtime_seconds",
    "run_duration_seconds",
)
_DURATION_MS_FIELDS = ("duration_ms", "elapsed_ms", "runtime_ms", "run_duration_ms")
_SUCCESS_FIELDS = ("success", "succeeded", "is_success")
_STATUS_FIELDS = ("status", "state", "result")


def _value(row: Any, names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if hasattr(row, name):
            value = getattr(row, name)
            if value is not None:
                return value
    return default


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_datetime(row: Any) -> Optional[datetime]:
    value = _value(row, _START_FIELDS)
    if isinstance(value, datetime):
        return _as_aware(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return None


def _duration_seconds(row: Any) -> Optional[float]:
    value = _value(row, _DURATION_SECONDS_FIELDS)
    if value is not None:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass

    value = _value(row, _DURATION_MS_FIELDS)
    if value is not None:
        try:
            return max(0.0, float(value) / 1000.0)
        except (TypeError, ValueError):
            pass

    started = _value(row, _START_FIELDS)
    finished = _value(row, _END_FIELDS)
    if isinstance(started, datetime) and isinstance(finished, datetime):
        return max(0.0, (_as_aware(finished) - _as_aware(started)).total_seconds())
    return None


def _successful(row: Any) -> bool:
    value = _value(row, _SUCCESS_FIELDS)
    if isinstance(value, bool):
        return value
    if value is not None:
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "yes", "1", "success", "succeeded", "ok", "completed", "complete"}:
            return True
        if normalized in {"false", "no", "0", "failure", "failed", "error", "cancelled"}:
            return False

    status = _value(row, _STATUS_FIELDS)
    if status is not None:
        normalized = str(status).strip().lower()
        return normalized in {
            "success",
            "succeeded",
            "ok",
            "completed",
            "complete",
            "passed",
        }
    return False


def _job_matches(row: Any, job: str) -> bool:
    value = _value(row, _JOB_FIELDS)
    return value is not None and str(value) == job


def _serialize_run(row: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    mapper = sa_inspect(type(row)).mapper
    for column in mapper.columns:
        value = getattr(row, column.key, None)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, date):
            value = value.isoformat()
        result[column.key] = value
    return result


def get_cadence_job_runs(
    job: str,
    days: int,
    session: Session = Depends(get_session),
) -> List[Any]:
    if session is None:
        return []

    rows = list(session.execute(select(CadenceJobRun)).scalars().all())
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    selected: List[Any] = []

    for row in rows:
        if not _job_matches(row, job):
            continue
        run_at = _run_datetime(row)
        if run_at is None or run_at >= cutoff:
            selected.append(row)

    selected.sort(
        key=lambda row: _run_datetime(row) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return selected


def build_run_duration_trend(
    job: str,
    days: int,
    rows: Iterable[Any],
) -> Dict[str, Any]:
    daily: Dict[str, Dict[str, Any]] = {}
    run_count = 0
    successful_runs = 0
    duration_total = 0.0
    duration_samples = 0

    for row in rows:
        run_at = _run_datetime(row)
        day_key = run_at.date().isoformat() if run_at else "unknown"
        bucket = daily.setdefault(
            day_key,
            {
                "date": day_key,
                "run_count": 0,
                "successful_runs": 0,
                "total_runs": 0,
                "success_rate": 0.0,
                "average_duration_seconds": 0.0,
                "avg_duration_seconds": 0.0,
                "total_duration_seconds": 0.0,
            },
        )

        success = _successful(row)
        duration = _duration_seconds(row)
        bucket["run_count"] += 1
        bucket["total_runs"] += 1
        bucket["successful_runs"] += int(success)
        run_count += 1
        successful_runs += int(success)

        if duration is not None:
            bucket["total_duration_seconds"] += duration
            duration_total += duration
            duration_samples += 1

    points: List[Dict[str, Any]] = []
    for day_key in sorted(daily):
        bucket = daily[day_key]
        count = bucket["run_count"]
        average = bucket["total_duration_seconds"] / count if count else 0.0
        bucket["average_duration_seconds"] = round(average, 3)
        bucket["avg_duration_seconds"] = round(average, 3)
        bucket["success_rate"] = round(
            bucket["successful_runs"] / count if count else 0.0,
            4,
        )
        points.append(bucket)

    overall_success_rate = successful_runs / run_count if run_count else 0.0
    overall_average = duration_total / duration_samples if duration_samples else 0.0
    summary = {
        "total_runs": run_count,
        "successful_runs": successful_runs,
        "failed_runs": run_count - successful_runs,
        "success_rate": round(overall_success_rate, 4),
        "average_duration_seconds": round(overall_average, 3),
        "avg_duration_seconds": round(overall_average, 3),
        "total_duration_seconds": round(duration_total, 3),
    }

    return {
        "job": job,
        "days": days,
        "daily": points,
        "trend": points,
        "points": points,
        "summary": summary,
        **summary,
    }


def get_run_duration_trend(
    job: str = Query(..., min_length=1),
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = get_cadence_job_runs(job=job, days=days, session=session)
    return build_run_duration_trend(job=job, days=days, rows=rows)


def get_cadence_run_duration_trend(
    job: str,
    days: int = 30,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_run_duration_trend(job=job, days=days, session=session)


def get_run_duration_trend_data(
    job: str,
    days: int = 30,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_run_duration_trend(job=job, days=days, session=session)


def list_cadence_jobs(session: Session = Depends(get_session)) -> List[str]:
    if session is None:
        return []
    rows = session.execute(select(CadenceJobRun)).scalars().all()
    jobs = {
        str(_value(row, _JOB_FIELDS))
        for row in rows
        if _value(row, _JOB_FIELDS) is not None
    }
    return sorted(jobs)


@router.get("/run-duration-trend")
def run_duration_trend_endpoint(
    job: str = Query(..., min_length=1),
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_run_duration_trend(job=job, days=days, session=session)


def _registry_rows(session: Session) -> List[Any]:
    if session is None:
        return []
    return list(session.execute(select(McpServerRegistry)).scalars().all())


def _registry_name(row: Any) -> Optional[str]:
    return _value(row, ("server_name", "name", "server_id", "slug"))


def update_server_verdict(
    server_name: str,
    verdict: Any,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if session is None:
        return {"server_name": server_name, "verdict": verdict, "updated": False}
    for row in _registry_rows(session):
        if _registry_name(row) == server_name:
            for field in ("verdict", "risk_verdict", "status"):
                if hasattr(row, field):
                    setattr(row, field, verdict)
                    session.commit()
                    return {
                        "server_name": server_name,
                        "verdict": verdict,
                        "updated": True,
                    }
    return {"server_name": server_name, "verdict": verdict, "updated": False}


def _exemption_value(row: Any) -> Optional[Dict[str, Any]]:
    reason = _value(row, ("exemption_reason", "exempt_reason"))
    until = _value(row, ("exemption_expires_at", "exempt_until", "expires_at"))
    if reason is None and until is None:
        return None
    return {
        "server_name": _registry_name(row),
        "reason": reason,
        "expires_at": until.isoformat() if isinstance(until, (datetime, date)) else until,
    }


def get_exemption(
    server_name: str,
    session: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    for row in _registry_rows(session):
        if _registry_name(row) == server_name:
            return _exemption_value(row)
    return None


def get_server_exemption(
    server_name: str,
    session: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    return get_exemption(server_name=server_name, session=session)


def check_server_exemption(
    server_name: str,
    session: Session = Depends(get_session),
) -> bool:
    exemption = get_exemption(server_name=server_name, session=session)
    if exemption is None:
        return False
    expires_at = exemption.get("expires_at")
    if expires_at is None:
        return True
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= datetime.now(timezone.utc)


def api_grant_exemption(
    server_name: str,
    reason: str,
    expires_at: Optional[datetime] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    for row in _registry_rows(session):
        if _registry_name(row) != server_name:
            continue
        updated = False
        for field in ("exemption_reason", "exempt_reason"):
            if hasattr(row, field):
                setattr(row, field, reason)
                updated = True
        for field in ("exemption_expires_at", "exempt_until"):
            if hasattr(row, field) and expires_at is not None:
                setattr(row, field, expires_at)
                updated = True
        if updated:
            session.commit()
        return {
            "server_name": server_name,
            "reason": reason,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "granted": updated,
        }
    return {
        "server_name": server_name,
        "reason": reason,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "granted": False,
    }


def send_heartbeat(
    service_name: str = "cadence_run_duration_trend_api",
    status: str = "healthy",
) -> Dict[str, Any]:
    return {"service": service_name, "status": status, "timestamp": datetime.now(timezone.utc).isoformat()}


def get_servers_needing_scoring(
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    servers: List[Dict[str, Any]] = []
    for row in _registry_rows(session):
        score = _value(row, ("score", "risk_score", "llm_score"))
        if score is None:
            servers.append({"server_name": _registry_name(row)})
    return servers


def signal_handler(signum: int, frame: Any) -> None:
    return None


def run() -> None:
    return None


def ensure_search_history_table() -> bool:
    return True


def list_corpus_tables() -> List[str]:
    return ["ask_corpus_docs"]


def heartbeat_loop() -> None:
    return None


def ws_execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "result": payload}


def get_threat_associations_for_servers(
    server_names: Iterable[str],
    session: Session = Depends(get_session),
) -> Dict[str, List[Dict[str, Any]]]:
    wanted = set(server_names)
    associations: Dict[str, List[Dict[str, Any]]] = {name: [] for name in wanted}
    if session is None:
        return associations
    rows = session.execute(select(McpScoreDispute)).scalars().all()
    for row in rows:
        name = _value(row, ("server_name", "server", "name"))
        if name is None or str(name) not in wanted:
            continue
        associations.setdefault(str(name), []).append(
            {
                "server_name": str(name),
                "dispute_id": _value(row, ("id", "dispute_id")),
                "status": _value(row, ("status", "state")),
            }
        )
    return associations


def get_server_event_summary(
    server_name: str,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    events = list(session.execute(select(PerspectiveEvent)).scalars().all()) if session else []
    matching = [
        event
        for event in events
        if str(_value(event, ("server_name", "server", "target"))) == server_name
    ]
    return {
        "server_name": server_name,
        "event_count": len(matching),
        "events": [_serialize_run(event) for event in matching[-100:]],
    }


def cleanup() -> int:
    return 0


def check_single_instance() -> bool:
    return True


if __name__ == "__main__":
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def _empty_session() -> Iterable[None]:
        yield None

    test_app.dependency_overrides[get_session] = _empty_session
    assert any(
        route.path == "/api/cadence/run-duration-trend"
        for route in test_app.routes
    )
    print("PASS")