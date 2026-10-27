from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import get_session

try:
    from app.models import AuditLog
except ImportError:
    AuditLog = None

router = APIRouter()

WINDOW_HOURS = 24


class ActionCount(BaseModel):
    action: str
    count: int


class TargetActivity(BaseModel):
    target_server_id: str
    entries: int
    actions: List[ActionCount]
    first_at: str
    last_at: str


class TargetActivityResponse(BaseModel):
    generated_at: str
    window_hours: int
    targets: List[TargetActivity]
    total_entries: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: Any) -> str:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")

    if value is None:
        return _utc_now().isoformat().replace("+00:00", "Z")

    raw = str(value).strip()
    if not raw:
        return _utc_now().isoformat().replace("+00:00", "Z")

    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return raw

    return _as_utc(parsed).isoformat().replace("+00:00", "Z")


def _row_value(row: Any, name: str) -> Any:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        try:
            return mapping[name]
        except (KeyError, TypeError):
            pass
    return getattr(row, name, None)


def _query_recent_activity(session: Any, cutoff: datetime) -> Iterable[Tuple[Any, Any, int, Any, Any]]:
    statement = text(
        """
        SELECT
            target_server_id,
            action,
            COUNT(*) AS entry_count,
            MIN(timestamp) AS first_at,
            MAX(timestamp) AS last_at
        FROM audit_log
        WHERE timestamp >= :cutoff
        GROUP BY target_server_id, action
        """
    )
    return session.execute(statement, {"cutoff": cutoff}).fetchall()


def _build_target_activity(session: Any, window_hours: int = WINDOW_HOURS) -> Dict[str, Any]:
    generated_at = _utc_now()
    cutoff = generated_at - timedelta(hours=window_hours)

    grouped: Dict[str, Dict[str, Any]] = {}

    for row in _query_recent_activity(session, cutoff):
        raw_target = _row_value(row, "target_server_id")
        target_id = "unknown" if raw_target is None else str(raw_target)

        raw_action = _row_value(row, "action")
        action_name = "unknown" if raw_action is None else str(raw_action)
        count = int(_row_value(row, "entry_count") or 0)
        first_at = _row_value(row, "first_at")
        last_at = _row_value(row, "last_at")

        target = grouped.setdefault(
            target_id,
            {
                "entries": 0,
                "actions": {},
                "first_at": None,
                "last_at": None,
            },
        )

        target["entries"] += count
        target["actions"][action_name] = target["actions"].get(action_name, 0) + count

        if first_at is not None:
            if target["first_at"] is None or first_at < target["first_at"]:
                target["first_at"] = first_at

        if last_at is not None:
            if target["last_at"] is None or last_at > target["last_at"]:
                target["last_at"] = last_at

    targets: List[Dict[str, Any]] = []
    total_entries = 0

    for target_id, target in grouped.items():
        entries = int(target["entries"])
        total_entries += entries

        first_at = target["first_at"]
        last_at = target["last_at"]

        actions = [
            {"action": action, "count": count}
            for action, count in sorted(
                target["actions"].items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

        targets.append(
            {
                "target_server_id": target_id,
                "entries": entries,
                "actions": actions,
                "first_at": _iso8601(first_at),
                "last_at": _iso8601(last_at),
            }
        )

    targets.sort(
        key=lambda target: (
            target["target_server_id"] == "unknown",
            target["target_server_id"],
        )
    )

    return {
        "generated_at": _iso8601(generated_at),
        "window_hours": int(window_hours),
        "targets": targets,
        "total_entries": total_entries,
    }


@router.get(
    "/audit/target-activity",
    response_model=TargetActivityResponse,
    summary="Get recent audit activity grouped by target server",
)
def get_target_activity(
    session: Any = Depends(get_session),
) -> TargetActivityResponse:
    return TargetActivityResponse(**_build_target_activity(session, WINDOW_HOURS))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    target_server_id VARCHAR NULL,
                    action VARCHAR NOT NULL
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO audit_log (timestamp, target_server_id, action)
                VALUES (:timestamp, :target_server_id, :action)
                """
            ),
            [
                {
                    "timestamp": now - timedelta(hours=1),
                    "target_server_id": "server-a",
                    "action": "login",
                },
                {
                    "timestamp": now - timedelta(hours=2),
                    "target_server_id": "server-a",
                    "action": "login",
                },
                {
                    "timestamp": now - timedelta(hours=3),
                    "target_server_id": "server-a",
                    "action": "scan",
                },
                {
                    "timestamp": now - timedelta(hours=4),
                    "target_server_id": None,
                    "action": "logout",
                },
                {
                    "timestamp": now - timedelta(hours=48),
                    "target_server_id": "server-a",
                    "action": "old",
                },
            ],
        )

    def get_test_session():
        with test_engine.connect() as connection:
            yield connection

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = get_test_session

    response = TestClient(test_app).get("/api/audit/target-activity")
    response.raise_for_status()
    payload = response.json()

    known = next(
        target
        for target in payload["targets"]
        if target["target_server_id"] == "server-a"
    )
    unknown = next(
        target
        for target in payload["targets"]
        if target["target_server_id"] == "unknown"
    )

    assert known["entries"] == 3
    assert unknown["entries"] == 1
    assert payload["total_entries"] == 4
    print("PASS")