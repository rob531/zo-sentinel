"""Cadence cost attribution service.

This module reads authoritative application state through ``app.db`` and reads
pipeline/mesh state through the ZoComputer write service.  It intentionally
contains no local database, engine, or replacement model definitions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import signal
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text

from app.db import get_session
from app.models import (
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    FreshnessStat,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    McpUnauthAccess,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)


router = APIRouter()

_STOP_EVENT = threading.Event()
_MESH_URL = "http://127.0.0.1:8772/query"
_MESH_TIMEOUT_SECONDS = 5.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _column_names(model: Any) -> set[str]:
    try:
        return {str(column.name).lower() for column in sa_inspect(model).columns}
    except (AttributeError, TypeError):
        return set()


def _first_present(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return None


def _model_row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return {
            column.key: getattr(row, column.key)
            for column in sa_inspect(type(row)).columns
        }
    except (AttributeError, TypeError):
        try:
            return {
                column.name: getattr(row, column.name)
                for column in row.__table__.columns
            }
        except AttributeError:
            return {}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on", "active", "granted", "enabled"}


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _server_id_from_row(row: Mapping[str, Any]) -> str:
    value = _first_present(
        row,
        (
            "server_id",
            "mcp_server_id",
            "registry_id",
            "target_server_id",
            "server_registry_id",
        ),
    )
    return str(value) if value is not None else ""


def _server_name_from_row(row: Mapping[str, Any]) -> str:
    value = _first_present(
        row,
        (
            "server_name",
            "name",
            "display_name",
            "hostname",
            "server_registry_name",
        ),
    )
    return str(value) if value is not None else ""


def _job_status(row: Mapping[str, Any]) -> str:
    value = _first_present(
        row,
        ("status", "state", "job_status", "result", "outcome", "run_status"),
    )
    normalized = str(value or "unknown").strip().lower().replace("-", "_")
    if normalized in {"ok", "passed", "pass", "success", "succeeded", "completed"}:
        return "succeeded"
    if normalized in {"failed", "failure", "error", "errored", "timed_out", "timeout"}:
        return "failed"
    if normalized in {"running", "in_progress", "started", "pending", "queued"}:
        return "running"
    if normalized in {"cancelled", "canceled", "skipped"}:
        return "skipped"
    return normalized or "unknown"


def _job_cost(row: Mapping[str, Any]) -> float:
    value = _first_present(
        row,
        (
            "cost_usd",
            "total_cost_usd",
            "estimated_cost_usd",
            "actual_cost_usd",
            "llm_cost_usd",
            "cost",
            "total_cost",
        ),
    )
    return max(0.0, _as_float(value))


def _job_duration_ms(row: Mapping[str, Any]) -> float:
    direct = _first_present(
        row,
        (
            "duration_ms",
            "elapsed_ms",
            "latency_ms",
            "execution_time_ms",
            "runtime_ms",
        ),
    )
    if direct is not None:
        return max(0.0, _as_float(direct))
    seconds = _first_present(
        row,
        ("duration_seconds", "elapsed_seconds", "runtime_seconds", "latency_seconds"),
    )
    if seconds is not None:
        return max(0.0, _as_float(seconds) * 1000.0)
    started = _as_datetime(
        _first_present(row, ("started_at", "start_time", "created_at", "queued_at"))
    )
    completed = _as_datetime(
        _first_present(
            row,
            ("completed_at", "finished_at", "end_time", "updated_at"),
        )
    )
    if started and completed:
        return max(0.0, (completed - started).total_seconds() * 1000.0)
    return 0.0


def _job_timestamp(row: Mapping[str, Any]) -> Optional[datetime]:
    return _as_datetime(
        _first_present(
            row,
            (
                "completed_at",
                "finished_at",
                "started_at",
                "start_time",
                "created_at",
                "updated_at",
                "run_at",
            ),
        )
    )


def _registry_timestamp(row: Mapping[str, Any]) -> Optional[datetime]:
    return _as_datetime(
        _first_present(
            row,
            (
                "last_heartbeat_at",
                "heartbeat_at",
                "last_seen_at",
                "last_scored_at",
                "last_score_at",
                "updated_at",
            ),
        )
    )


def _registry_cadence_seconds(row: Mapping[str, Any]) -> int:
    value = _first_present(
        row,
        (
            "scoring_cadence_seconds",
            "scoring_cadence",
            "cadence_seconds",
            "score_interval_seconds",
            "heartbeat_interval_seconds",
        ),
    )
    seconds = _as_int(value, 300)
    return seconds if seconds > 0 else 300


def _registry_is_idle(row: Mapping[str, Any], now: datetime) -> bool:
    explicit = _first_present(row, ("idle", "is_idle"))
    if explicit is not None:
        return _as_bool(explicit)
    status = str(
        _first_present(row, ("status", "server_status", "state")) or ""
    ).strip().lower()
    if status in {"offline", "disabled", "inactive", "idle"}:
        return True
    timestamp = _registry_timestamp(row)
    if timestamp is None:
        return False
    return now - timestamp > timedelta(seconds=_registry_cadence_seconds(row) * 2)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


@contextmanager
def _session_scope(session: Any = None) -> Iterator[Any]:
    if session is not None:
        yield session
        return

    candidate = get_session()
    if inspect.isawaitable(candidate):
        raise RuntimeError("get_session returned an awaitable; use an async endpoint")
    generator = candidate if hasattr(candidate, "__next__") else None
    owned_session = next(generator) if generator is not None else candidate
    try:
        yield owned_session
    finally:
        if generator is not None:
            try:
                generator.close()
            except Exception:
                pass
        elif hasattr(owned_session, "close"):
            try:
                owned_session.close()
            except Exception:
                pass


def _select_rows(session: Any, model: Any) -> List[Dict[str, Any]]:
    result = session.execute(select(model))
    return [_model_row_dict(row) for row in result.scalars().all()]


def _filter_by_server(
    session: Any, model: Any, server_id: str
) -> List[Dict[str, Any]]:
    rows = _select_rows(session, model)
    return [row for row in rows if _server_id_from_row(row) == str(server_id)]


def _find_server_row(session: Any, server_id: str) -> Optional[Dict[str, Any]]:
    for row in _filter_by_server(session, McpServerRegistry, server_id):
        return row
    return None


def _set_model_value(row: Any, candidates: Sequence[str], value: Any) -> bool:
    columns = _column_names(type(row))
    for candidate in candidates:
        if candidate.lower() in columns:
            setattr(row, candidate, value)
            return True
    return False


def _update_registry(
    session: Any,
    server_id: str,
    values: Sequence[tuple[Sequence[str], Any]],
) -> Optional[Dict[str, Any]]:
    rows = _filter_by_server(session, McpServerRegistry, server_id)
    if not rows:
        return None
    row = rows[0]
    for candidates, value in values:
        _set_model_value(row, candidates, value)
    session.commit()
    return _model_row_dict(row)


def _mesh_query(
    query: str,
    params: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    payload = {"query": query, "params": dict(params or {})}
    request = urllib.request.Request(
        _MESH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_MESH_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        fallback_payload = {"sql": query, "params": dict(params or {})}
        fallback_request = urllib.request.Request(
            _MESH_URL,
            data=json.dumps(fallback_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                fallback_request, timeout=_MESH_TIMEOUT_SECONDS
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as fallback_error:
            raise RuntimeError(
                f"mesh query failed ({error}); fallback failed ({fallback_error})"
            ) from fallback_error
    except Exception as error:
        raise RuntimeError(f"mesh query failed: {error}") from error

    if isinstance(body, list):
        rows = body
    elif isinstance(body, Mapping):
        rows = body.get("rows")
        if rows is None:
            rows = body.get("data")
        if rows is None and isinstance(body.get("result"), list):
            rows = body["result"]
        if rows is None:
            rows = []
    else:
        rows = []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def build_cost_attribution(
    session: Any,
    server_id: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Build per-job cost and throughput attribution from authoritative data."""

    now = _utcnow()
    registry_rows = _select_rows(session, McpServerRegistry)
    job_rows = _select_rows(session, CadenceJobRun)

    if server_id is not None:
        registry_rows = [
            row
            for row in registry_rows
            if _server_id_from_row(row) == str(server_id)
            or _server_name_from_row(row) == str(server_id)
        ]
        job_rows = [
            row for row in job_rows if _server_id_from_row(row) == str(server_id)
        ]

    registry_by_id = {
        _server_id_from_row(row): row
        for row in registry_rows
        if _server_id_from_row(row)
    }
    jobs_by_server: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for job in job_rows:
        jobs_by_server[_server_id_from_row(job)].append(job)

    server_names = {
        _server_id_from_row(row): _server_name_from_row(row)
        for row in registry_rows
    }

    aggregate_rows: List[Dict[str, Any]] = []
    all_server_ids = set(registry_by_id) | set(jobs_by_server)

    for current_server_id in all_server_ids:
        registry = registry_by_id.get(current_server_id, {})
        jobs = jobs_by_server.get(current_server_id, [])
        statuses = [_job_status(job) for job in jobs]
        costs = [_job_cost(job) for job in jobs]
        durations = [_job_duration_ms(job) for job in jobs]
        timestamps = [
            timestamp
            for timestamp in (_job_timestamp(job) for job in jobs)
            if timestamp is not None
        ]

        successful = sum(status == "succeeded" for status in statuses)
        failed = sum(status == "failed" for status in statuses)
        running = sum(status == "running" for status in statuses)
        skipped = sum(status == "skipped" for status in statuses)
        completed = successful + failed
        elapsed_hours = 1.0
        if timestamps:
            elapsed_hours = max(
                (now - min(timestamps)).total_seconds() / 3600.0, 1.0 / 60.0
            )
        throughput = completed / elapsed_hours

        aggregate_rows.append(
            {
                "server_id": current_server_id,
                "server_name": server_names.get(current_server_id)
                or _server_name_from_row(registry),
                "total_jobs": len(jobs),
                "successful_jobs": successful,
                "failed_jobs": failed,
                "running_jobs": running,
                "skipped_jobs": skipped,
                "failure_rate": round(failed / completed, 6) if completed else 0.0,
                "success_rate": round(
                    successful / completed, 6
                ) if completed else 0.0,
                "total_cost_usd": round(sum(costs), 8),
                "average_cost_usd": round(
                    sum(costs) / len(costs), 8
                ) if costs else 0.0,
                "average_duration_ms": round(
                    statistics.fmean(durations), 3
                ) if durations else 0.0,
                "p95_duration_ms": round(_percentile(durations, 0.95), 3),
                "throughput_jobs_per_hour": round(throughput, 6),
                "idle": _registry_is_idle(registry, now),
                "last_job_at": _json_value(max(timestamps) if timestamps else None),
                "last_heartbeat_at": _json_value(_registry_timestamp(registry)),
            }
        )

    aggregate_rows.sort(
        key=lambda row: (
            row["total_cost_usd"],
            row["failed_jobs"],
            row["throughput_jobs_per_hour"],
        ),
        reverse=True,
    )

    expensive_jobs: List[Dict[str, Any]] = []
    failing_jobs: List[Dict[str, Any]] = []
    for job in job_rows:
        identifier = _first_present(
            job, ("id", "job_run_id", "run_id", "job_id", "cadence_job_run_id")
        )
        status = _job_status(job)
        job_summary = {
            "job_run_id": str(identifier) if identifier is not None else None,
            "server_id": _server_id_from_row(job),
            "status": status,
            "cost_usd": round(_job_cost(job), 8),
            "duration_ms": round(_job_duration_ms(job), 3),
            "started_at": _json_value(
                _as_datetime(_first_present(job, ("started_at", "start_time")))
            ),
            "completed_at": _json_value(
                _as_datetime(
                    _first_present(job, ("completed_at", "finished_at", "end_time"))
                )
            ),
            "error": _first_present(job, ("error", "error_message", "failure_reason")),
        }
        expensive_jobs.append(job_summary)
        if status == "failed":
            failing_jobs.append(job_summary)

    expensive_jobs.sort(key=lambda row: row["cost_usd"], reverse=True)
    failing_jobs.sort(
        key=lambda row: row["completed_at"] or row["started_at"] or "", reverse=True
    )

    mesh_signal_count = 0
    mesh_error: Optional[str] = None
    try:
        mesh_rows = _mesh_query(
            "SELECT * FROM mcp_signal_scores ORDER BY created_at DESC LIMIT 5000"
        )
        if server_id is None:
            mesh_signal_count = len(mesh_rows)
        else:
            mesh_signal_count = sum(
                _server_id_from_row(row) == str(server_id) for row in mesh_rows
            )
    except RuntimeError as error:
        mesh_error = str(error)

    total_cost = sum(row["total_cost_usd"] for row in aggregate_rows)
    total_jobs = sum(row["total_jobs"] for row in aggregate_rows)
    total_failed = sum(row["failed_jobs"] for row in aggregate_rows)
    idle_servers = sum(row["idle"] for row in aggregate_rows)

    return {
        "generated_at": _json_value(now),
        "summary": {
            "server_count": len(aggregate_rows),
            "total_jobs": total_jobs,
            "failed_jobs": total_failed,
            "idle_servers": idle_servers,
            "total_cost_usd": round(total_cost, 8),
            "average_cost_per_job_usd": round(
                total_cost / total_jobs, 8
            ) if total_jobs else 0.0,
            "mesh_signal_count": mesh_signal_count,
        },
        "servers": aggregate_rows[: max(1, min(limit, 1000))],
        "expensive_jobs": expensive_jobs[: max(1, min(limit, 1000))],
        "failing_jobs": failing_jobs[: max(1, min(limit, 1000))],
        "idle_servers": [
            row for row in aggregate_rows if row["idle"]
        ][: max(1, min(limit, 1000))],
        "mesh_error": mesh_error,
    }


def get_exemption(server_id: str, session: Any = None, **kwargs: Any) -> Dict[str, Any]:
    """Return the effective scoring exemption for a registered server."""

    with _session_scope(session) as db:
        row = _find_server_row(db, server_id)
        if row is None:
            return {
                "server_id": str(server_id),
                "exempt": False,
                "exemption_type": None,
                "expires_at": None,
                "found": False,
            }
        exemption_type = _first_present(
            row,
            (
                "exemption_type",
                "exemption_reason",
                "score_exemption_type",
                "scoring_exemption",
            ),
        )
        expires_at = _as_datetime(
            _first_present(
                row,
                (
                    "exemption_expires_at",
                    "exemption_expiry",
                    "score_exemption_expires_at",
                    "exemption_until",
                ),
            )
        )
        explicit = _first_present(
            row,
            (
                "exempt",
                "is_exempt",
                "scoring_exempt",
                "is_scoring_exempt",
                "exemption_active",
            ),
        )
        exempt = _as_bool(explicit) if explicit is not None else exemption_type is not None
        if expires_at is not None and expires_at <= _utcnow():
            exempt = False
        return {
            "server_id": str(server_id),
            "exempt": exempt,
            "exemption_type": exemption_type,
            "reason": _first_present(row, ("exemption_reason", "exemption_notes")),
            "expires_at": _json_value(expires_at),
            "found": True,
        }


def get_server_exemption(
    server_id: str, session: Any = None, **kwargs: Any
) -> Dict[str, Any]:
    return get_exemption(server_id, session=session, **kwargs)


def check_server_exemption(
    server_id: str, session: Any = None, **kwargs: Any
) -> bool:
    return bool(get_exemption(server_id, session=session, **kwargs).get("exempt"))


def api_grant_exemption(
    server_id: str,
    session: Any = None,
    exemption_type: Optional[str] = None,
    reason: Optional[str] = None,
    expires_at: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Grant a scoring exemption using fields present on the real registry model."""

    with _session_scope(session) as db:
        rows = _filter_by_server(db, McpServerRegistry, server_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Server not found")
        row = rows[0]
        values: List[tuple[Sequence[str], Any]] = [
            (("exempt", "is_exempt", "scoring_exempt", "is_scoring_exempt"), True),
            (
                ("exemption_type", "score_exemption_type", "scoring_exemption"),
                exemption_type or "administrative",
            ),
            (("exemption_reason", "exemption_notes"), reason),
        ]
        if expires_at is not None:
            values.append(
                (
                    (
                        "exemption_expires_at",
                        "exemption_expiry",
                        "score_exemption_expires_at",
                        "exemption_until",
                    ),
                    _as_datetime(expires_at),
                )
            )
        for candidates, value in values:
            if value is not None:
                _set_model_value(row, candidates, value)
        db.commit()
        result = _model_row_dict(row)
        return {
            "server_id": str(server_id),
            "granted": True,
            "exemption_type": exemption_type or "administrative",
            "reason": reason,
            "expires_at": _json_value(
                _as_datetime(
                    _first_present(
                        result,
                        (
                            "exemption_expires_at",
                            "exemption_expiry",
                            "score_exemption_expires_at",
                            "exemption_until",
                        ),
                    )
                )
            ),
        }


def update_server_verdict(
    server_id: str,
    verdict: Optional[str] = None,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Update the persisted server verdict/risk disposition."""

    with _session_scope(session) as db:
        rows = _filter_by_server(db, McpServerRegistry, server_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Server not found")
        row = rows[0]
        value = verdict if verdict is not None else kwargs.get("risk_tier")
        if value is None:
            raise ValueError("verdict or risk_tier is required")
        changed = _set_model_value(
            row,
            (
                "verdict",
                "current_verdict",
                "server_verdict",
                "risk_tier",
                "current_risk_tier",
                "risk_level",
            ),
            value,
        )
        if changed:
            db.commit()
        return {
            "server_id": str(server_id),
            "verdict": value,
            "updated": changed,
        }


def send_heartbeat(
    server_id: Optional[str] = None,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Record a server heartbeat in the authoritative registry."""

    timestamp = _utcnow()
    with _session_scope(session) as db:
        if server_id is None:
            return {"ok": True, "recorded_at": _json_value(timestamp)}
        rows = _filter_by_server(db, McpServerRegistry, server_id)
        if not rows:
            return {
                "ok": False,
                "server_id": str(server_id),
                "recorded_at": _json_value(timestamp),
                "reason": "server_not_found",
            }
        row = rows[0]
        changed = _set_model_value(
            row,
            (
                "last_heartbeat_at",
                "heartbeat_at",
                "last_seen_at",
                "last_seen",
            ),
            timestamp,
        )
        if changed:
            db.commit()
        return {
            "ok": True,
            "server_id": str(server_id),
            "recorded_at": _json_value(timestamp),
            "updated": changed,
        }


def get_servers_needing_scoring(
    session: Any = None,
    limit: int = 100,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    now = _utcnow()
    with _session_scope(session) as db:
        rows = _select_rows(db, McpServerRegistry)
        due: List[Dict[str, Any]] = []
        for row in rows:
            if _registry_is_idle(row, now) and _as_bool(
                _first_present(row, ("idle", "is_idle"))
            ):
                continue
            timestamp = _registry_timestamp(row)
            if timestamp is None:
                due.append(_model_row_dict(row))
                continue
            age = (now - timestamp).total_seconds()
            if age >= _registry_cadence_seconds(row):
                due.append(_model_row_dict(row))
        return due[: max(1, min(limit, 1000))]


def answer_trust_question(
    question: str,
    server_id: Optional[str] = None,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Answer a trust question from persisted scoring and dispute state."""

    with _session_scope(session) as db:
        score_rows = _select_rows(db, McpLlmAxisScore)
        dispute_rows = _select_rows(db, McpScoreDispute)
        if server_id is not None:
            score_rows = [
                row
                for row in score_rows
                if _server_id_from_row(row) == str(server_id)
            ]
            dispute_rows = [
                row
                for row in dispute_rows
                if _server_id_from_row(row) == str(server_id)
            ]

        scores: List[float] = []
        for row in score_rows:
            value = _first_present(row, ("score", "value", "axis_score", "rating"))
            if value is not None:
                scores.append(_as_float(value))
        average_score = statistics.fmean(scores) if scores else 0.0
        open_disputes = sum(
            str(
                _first_present(row, ("status", "state")) or "open"
            ).lower()
            not in {"resolved", "closed", "dismissed"}
            for row in dispute_rows
        )

        if not score_rows:
            answer = "No persisted trust scores are available for this server."
        elif open_disputes:
            answer = (
                "Trust evidence exists, but unresolved score disputes require review "
                "before relying on the current score."
            )
        elif average_score >= 0.75:
            answer = "The persisted trust evidence supports a positive assessment."
        elif average_score >= 0.5:
            answer = "The persisted trust evidence supports a qualified assessment."
        else:
            answer = "The persisted trust evidence indicates elevated concern."

        return {
            "question": question,
            "server_id": server_id,
            "answer": answer,
            "score_count": len(score_rows),
            "average_score": round(average_score, 6),
            "open_disputes": open_disputes,
            "dispute_count": len(dispute_rows),
            "evidence": {
                "scores": [_json_value(row) for row in score_rows[:20]],
                "disputes": [_json_value(row) for row in dispute_rows[:20]],
            },
        }


def signal_handler(signum: int = signal.SIGTERM, frame: Any = None) -> None:
    """Request graceful termination of background workers."""

    _STOP_EVENT.set()


def run(
    interval_seconds: int = 60,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run one cadence attribution/heartbeat worker iteration."""

    started_at = _utcnow()
    due = get_servers_needing_scoring(session=session, limit=1000)
    return {
        "ok": True,
        "started_at": _json_value(started_at),
        "completed_at": _json_value(_utcnow()),
        "servers_due": len(due),
        "interval_seconds": interval_seconds,
    }


def ensure_search_history_table(session: Any = None, **kwargs: Any) -> bool:
    """Ensure the mesh-side search-history relation exists."""

    _mesh_query(
        """
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            results TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return True


def list_corpus_tables(session: Any = None, **kwargs: Any) -> List[Dict[str, Any]]:
    """List corpus relations exposed by the mesh store."""

    rows = _mesh_query(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [{"name": str(_first_present(row, ("name", "table_name")) or "")} for row in rows]


async def ws_execute(
    payload: Any = None,
    websocket: Any = None,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute a supported read-only WebSocket command."""

    command = payload
    if isinstance(payload, Mapping):
        command = payload.get("command") or payload.get("action") or payload.get("type")

    if command in {"corpus_tables", "list_corpus_tables"}:
        result: Any = list_corpus_tables(session=session)
    elif command in {"scoring_due", "get_servers_needing_scoring"}:
        result = get_servers_needing_scoring(session=session)
    elif command in {"cost_attribution", "attribution"}:
        with _session_scope(session) as db:
            result = build_cost_attribution(db)
    else:
        result = {
            "ok": False,
            "error": "unsupported_command",
            "supported_commands": [
                "corpus_tables",
                "scoring_due",
                "cost_attribution",
            ],
        }

    response = {"ok": not (isinstance(result, Mapping) and result.get("ok") is False), "result": result}
    if websocket is not None and hasattr(websocket, "send_json"):
        await websocket.send_json(response)
    return response


def get_threat_associations_for_servers(
    server_ids: Optional[Iterable[str]] = None,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return persisted threat-intelligence references grouped by server."""

    requested = {str(item) for item in server_ids} if server_ids is not None else None
    with _session_scope(session) as db:
        reference_rows = _select_rows(db, ThreatIntelRef)
        server_rows = _select_rows(db, McpServerRegistry)
        result: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in reference_rows:
            associated_server = _first_present(
                row,
                ("server_id", "mcp_server_id", "registry_id", "target_server_id"),
            )
            if associated_server is None:
                continue
            key = str(associated_server)
            if requested is not None and key not in requested:
                continue
            result[key].append(_json_value(row))
        if requested is not None:
            known_server_ids = {
                _server_id_from_row(row)
                for row in server_rows
                if _server_id_from_row(row)
            }
            for server_id in requested - known_server_ids:
                result.setdefault(server_id, [])
        return dict(result)


def get_server_event_summary(
    server_id: str,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Summarize persisted scoring and perspective events for a server."""

    with _session_scope(session) as db:
        job_rows = _filter_by_server(db, CadenceJobRun, server_id)
        perspective_rows = _filter_by_server(db, PerspectiveEvent, server_id)
        event_rows: List[Dict[str, Any]] = []
        for row in job_rows + perspective_rows:
            event_rows.append(
                {
                    "event_type": _first_present(
                        row,
                        ("event_type", "type", "job_type", "status", "state"),
                    ),
                    "status": _job_status(row),
                    "timestamp": _json_value(
                        _job_timestamp(row)
                        or _as_datetime(
                            _first_present(row, ("occurred_at", "event_at", "created_at"))
                        )
                    ),
                    "detail": _json_value(row),
                }
            )
        event_rows.sort(key=lambda row: row["timestamp"] or "", reverse=True)
        failures = [row for row in event_rows if row["status"] == "failed"]
        return {
            "server_id": str(server_id),
            "event_count": len(event_rows),
            "failure_count": len(failures),
            "events": event_rows[:200],
        }


def cleanup(
    retention_days: int = 30,
    session: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Delete cadence job runs older than the retention window."""

    days = max(1, _as_int(retention_days, 30))
    cutoff = _utcnow() - timedelta(days=days)
    with _session_scope(session) as db:
        rows = _select_rows(db, CadenceJobRun)
        stale = [
            row
            for row in rows
            if (_job_timestamp(row) is not None and _job_timestamp(row) < cutoff)
        ]
        deleted = 0
        for row in stale:
            identifier = _first_present(
                row, ("id", "job_run_id", "run_id", "job_id", "cadence_job_run_id")
            )
            if identifier is None:
                continue
            db.delete(row)
            deleted += 1
        if deleted:
            db.commit()
        return {
            "ok": True,
            "retention_days": days,
            "cutoff": _json_value(cutoff),
            "deleted": deleted,
        }


def check_single_instance(session: Any = None, **kwargs: Any) -> bool:
    """Check the PostgreSQL advisory lock used to enforce one active worker."""

    with _session_scope(session) as db:
        result = db.execute(text("SELECT pg_try_advisory_lock(743219865) AS acquired"))
        row = result.mappings().first()
        if row is None:
            return False
        return _as_bool(row.get("acquired"))


async def heartbeat_loop(
    interval_seconds: int = 60,
    session: Any = None,
    stop_event: Optional[threading.Event] = None,
    **kwargs: Any,
) -> None:
    """Run cadence heartbeats until a stop event is set."""

    stopper = stop_event or _STOP_EVENT
    interval = max(1, _as_int(interval_seconds, 60))
    while not stopper.is_set():
        try:
            await asyncio.to_thread(run, interval, session)
        except Exception:
            pass
        stopper.wait(interval)


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "cadence_cost_attribution",
        "timestamp": _json_value(_utcnow()),
    }


@router.get("/cadence-cost-attribution")
def cadence_cost_attribution(
    server_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return build_cost_attribution(session, server_id=server_id, limit=limit)


@router.get("/cadence-cost-attribution/{server_id}")
def cadence_cost_attribution_for_server(
    server_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return build_cost_attribution(session, server_id=server_id, limit=limit)


@router.post("/heartbeat")
def heartbeat(
    payload: Optional[Mapping[str, Any]] = None,
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    server_id = None
    if payload:
        server_id = payload.get("server_id")
    return send_heartbeat(server_id=server_id, session=session)


@router.get("/servers-needing-scoring")
def servers_needing_scoring(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Any = Depends(get_session),
) -> List[Dict[str, Any]]:
    return get_servers_needing_scoring(session=session, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI(title="Cadence Cost Attribution Self-Test")
    that_app.include_router(router)

    def _self_test_session():
        yield None

    that_app.dependency_overrides[get_session] = _self_test_session
    schema = that_app.openapi()
    assert "/cadence-cost-attribution" in schema["paths"]
    assert "/health" in schema["paths"]
    print("PASS")