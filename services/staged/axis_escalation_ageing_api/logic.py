from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api")

_SERVER_ID_COLUMNS = (
    "server_id",
    "mcp_server_id",
    "mcp_server_registry_id",
    "registry_id",
)
_SCORE_ID_COLUMNS = (
    "score_id",
    "axis_score_id",
    "mcp_llm_axis_score_id",
    "id",
)
_AXIS_COLUMNS = (
    "axis_name",
    "axis",
    "dimension",
    "metric",
    "name",
)
_SCORE_VALUE_COLUMNS = (
    "axis_score",
    "score",
    "value",
    "risk_score",
)
_STATUS_COLUMNS = (
    "escalation_status",
    "status",
    "state",
)
_SEVERITY_COLUMNS = (
    "severity",
    "risk_level",
    "risk_tier",
)
_DECISION_COLUMNS = (
    "decision",
    "action",
    "disposition",
)
_BOOLEAN_COLUMNS = (
    "is_escalated",
    "escalated",
    "requires_action",
    "needs_review",
)
_ESCALATED_AT_COLUMNS = (
    "escalated_at",
    "escalation_at",
    "disputed_at",
    "created_at",
    "updated_at",
    "scored_at",
)
_RESOLVED_AT_COLUMNS = (
    "resolved_at",
    "closed_at",
    "dismissed_at",
)
_NAME_COLUMNS = (
    "server_name",
    "name",
    "display_name",
    "hostname",
    "slug",
)

_ESCALATED_STATUSES = {
    "escalated",
    "open",
    "pending",
    "unresolved",
    "disputed",
    "needs_review",
    "review",
}
_RESOLVED_STATUSES = {
    "accepted",
    "closed",
    "denied",
    "dismissed",
    "rejected",
    "resolved",
    "withdrawn",
}
_ESCALATED_DECISIONS = {"escalate", "escalated", "review", "unresolved"}
_ESCALATED_SEVERITIES = {"critical", "escalated", "high", "severe"}


def _column_names(model: Any) -> set[str]:
    return set(model.__table__.columns.keys())


def _value(row: dict[str, Any], columns: tuple[str, ...]) -> Any:
    for column in columns:
        if column in row and row[column] is not None:
            return row[column]
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _timestamp(
    row: dict[str, Any],
    columns: tuple[str, ...],
) -> tuple[datetime | None, str | None]:
    for column in columns:
        if column in row and row[column] is not None:
            parsed = _as_utc(row[column])
            if parsed is not None:
                return parsed, column
    return None, None


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return _text(value) in {"1", "true", "yes", "y"}


def _is_explicitly_escalated(row: dict[str, Any]) -> bool:
    for column in _BOOLEAN_COLUMNS:
        if column in row and _is_true(row[column]):
            return True

    for column in _STATUS_COLUMNS:
        if column in row and _text(row[column]) in _ESCALATED_STATUSES:
            return True

    for column in _DECISION_COLUMNS:
        if column in row and _text(row[column]) in _ESCALATED_DECISIONS:
            return True

    for column in _SEVERITY_COLUMNS:
        if column in row and _text(row[column]) in _ESCALATED_SEVERITIES:
            return True

    return False


def _is_resolved_dispute(row: dict[str, Any]) -> bool:
    for column in _STATUS_COLUMNS:
        if column in row and _text(row[column]) in _RESOLVED_STATUSES:
            return True

    for column in _RESOLVED_AT_COLUMNS:
        if column in row and row[column] is not None:
            return True

    return False


def _same_identifier(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


def _dispute_matches_score(dispute: dict[str, Any], score: dict[str, Any]) -> bool:
    dispute_score_id = _value(dispute, _SCORE_ID_COLUMNS)
    score_id = _value(score, _SCORE_ID_COLUMNS)
    if dispute_score_id is not None and score_id is not None:
        if _same_identifier(dispute_score_id, score_id):
            return True

    dispute_server_id = _value(dispute, _SERVER_ID_COLUMNS)
    score_server_id = _value(score, _SERVER_ID_COLUMNS)
    dispute_axis = _value(dispute, _AXIS_COLUMNS)
    score_axis = _value(score, _AXIS_COLUMNS)

    if dispute_server_id is None or score_server_id is None:
        return False
    if not _same_identifier(dispute_server_id, score_server_id):
        return False
    if dispute_axis is None or score_axis is None:
        return False
    return _text(dispute_axis) == _text(score_axis)


def _age_bucket(age_days: float | None) -> str:
    if age_days is None:
        return "unknown"
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days <= 365:
        return "181-365"
    return "365+"


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def get_escalation_ageing_report(
    session: Session,
    days: int = 30,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - (days * 86400)

    score_result = session.execute(select(McpLlmAxisScore))
    server_result = session.execute(select(McpServerRegistry))
    dispute_result = session.execute(select(McpScoreDispute))

    scores = [dict(row) for row in score_result.mappings().all()]
    servers = [dict(row) for row in server_result.mappings().all()]
    disputes = [dict(row) for row in dispute_result.mappings().all()]

    server_by_id: dict[str, dict[str, Any]] = {}
    for server in servers:
        server_id = _value(server, _SERVER_ID_COLUMNS)
        if server_id is None:
            server_id = _value(server, ("id",))
        if server_id is not None:
            server_by_id[str(server_id)] = server

    unresolved_disputes = [
        dispute for dispute in disputes if not _is_resolved_dispute(dispute)
    ]
    resolved_disputes = [
        dispute for dispute in disputes if _is_resolved_dispute(dispute)
    ]

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for score in scores:
        score_id = _value(score, _SCORE_ID_COLUMNS)
        server_id = _value(score, _SERVER_ID_COLUMNS)
        axis = _value(score, _AXIS_COLUMNS)
        score_value = _value(score, _SCORE_VALUE_COLUMNS)

        matching_unresolved = [
            dispute
            for dispute in unresolved_disputes
            if _dispute_matches_score(dispute, score)
        ]
        matching_resolved = [
            dispute
            for dispute in resolved_disputes
            if _dispute_matches_score(dispute, score)
        ]

        if not _is_explicitly_escalated(score) and not matching_unresolved:
            continue
        if matching_resolved and not matching_unresolved:
            continue

        escalated_at, timestamp_column = _timestamp(score, _ESCALATED_AT_COLUMNS)
        if matching_unresolved:
            dispute_at, dispute_timestamp_column = _timestamp(
                matching_unresolved[0], _ESCALATED_AT_COLUMNS
            )
            if dispute_at is not None:
                escalated_at = dispute_at
                timestamp_column = dispute_timestamp_column

        if escalated_at is not None and escalated_at.timestamp() < cutoff:
            continue

        age_days: float | None = None
        if escalated_at is not None:
            age_days = round(
                max(0.0, (now - escalated_at).total_seconds() / 86400),
                2,
            )

        registry = server_by_id.get(str(server_id), {}) if server_id is not None else {}
        server_name = _value(score, _NAME_COLUMNS) or _value(registry, _NAME_COLUMNS)

        identity = str(score_id) if score_id is not None else "|".join(
            str(part)
            for part in (
                server_id,
                axis,
                escalated_at.isoformat() if escalated_at is not None else None,
            )
        )
        if identity in seen:
            continue
        seen.add(identity)

        items.append(
            {
                "score_id": _json_value(score_id),
                "server_id": _json_value(server_id),
                "server_name": _json_value(server_name),
                "axis": _json_value(axis),
                "score": _json_value(score_value),
                "status": "unresolved",
                "escalated_at": _json_value(escalated_at),
                "escalated_at_column": timestamp_column,
                "age_days": age_days,
                "age_bucket": _age_bucket(age_days),
            }
        )

    for dispute in unresolved_disputes:
        if any(_dispute_matches_score(dispute, score) for score in scores):
            continue

        dispute_score_id = _value(dispute, _SCORE_ID_COLUMNS)
        server_id = _value(dispute, _SERVER_ID_COLUMNS)
        axis = _value(dispute, _AXIS_COLUMNS)
        escalated_at, timestamp_column = _timestamp(dispute, _ESCALATED_AT_COLUMNS)

        if escalated_at is not None and escalated_at.timestamp() < cutoff:
            continue

        age_days: float | None = None
        if escalated_at is not None:
            age_days = round(
                max(0.0, (now - escalated_at).total_seconds() / 86400),
                2,
            )

        registry = server_by_id.get(str(server_id), {}) if server_id is not None else {}
        server_name = _value(dispute, _NAME_COLUMNS) or _value(registry, _NAME_COLUMNS)
        identity = str(dispute_score_id) if dispute_score_id is not None else "|".join(
            str(part)
            for part in (
                server_id,
                axis,
                escalated_at.isoformat() if escalated_at is not None else None,
            )
        )
        if identity in seen:
            continue
        seen.add(identity)

        items.append(
            {
                "score_id": _json_value(dispute_score_id),
                "server_id": _json_value(server_id),
                "server_name": _json_value(server_name),
                "axis": _json_value(axis),
                "score": None,
                "status": "unresolved",
                "escalated_at": _json_value(escalated_at),
                "escalated_at_column": timestamp_column,
                "age_days": age_days,
                "age_bucket": _age_bucket(age_days),
            }
        )

    items.sort(
        key=lambda item: (
            item["age_days"] is not None,
            item["age_days"] or 0,
        ),
        reverse=True,
    )

    buckets = {
        "0-7": 0,
        "8-30": 0,
        "31-90": 0,
        "91-180": 0,
        "181-365": 0,
        "365+": 0,
        "unknown": 0,
    }
    for item in items:
        buckets[item["age_bucket"]] += 1

    known_ages = [
        item["age_days"]
        for item in items
        if item["age_days"] is not None
    ]

    return {
        "window_days": days,
        "cutoff": _json_value(cutoff),
        "generated_at": _json_value(now),
        "total": len(items),
        "count": len(items),
        "oldest_age_days": max(known_ages) if known_ages else None,
        "buckets": buckets,
        "items": items,
    }


@router.get("/axis/escalation-ageing")
def escalation_ageing(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_escalation_ageing_report(session=session, days=days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    class _EmptyMappings:
        def all(self) -> list[dict[str, Any]]:
            return []

    class _EmptyResult:
        def mappings(self) -> _EmptyMappings:
            return _EmptyMappings()

    class _SelfTestSession:
        def execute(self, statement: Any) -> _EmptyResult:
            return _EmptyResult()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: _SelfTestSession()

    with TestClient(that_app) as client:
        response = client.get("/api/axis/escalation-ageing?days=30")
        assert response.status_code == 200
        payload = response.json()
        assert payload["window_days"] == 30
        assert payload["total"] == 0
        assert isinstance(payload["items"], list)

    print("PASS")