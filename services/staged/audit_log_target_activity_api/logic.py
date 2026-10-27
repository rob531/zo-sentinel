from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401

router = APIRouter(prefix="/api", tags=["audit"])


class ActionCount(BaseModel):
    action: str
    count: int


class TargetActivity(BaseModel):
    target_server_id: str
    entries: int
    actions: list[ActionCount]
    first_at: str
    last_at: str


class TargetActivityResponse(BaseModel):
    generated_at: str
    window_hours: int
    targets: list[TargetActivity]
    total_entries: int


def _as_iso8601(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text_value = str(value).strip()
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text_value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _target_key(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _action_key(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "unknown"
    return str(value)


def get_target_activity(
    db: Session = Depends(get_session),
    window_hours: int = 24,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    rows = db.execute(
        text(
            """
            SELECT
                target_server_id,
                action,
                actor,
                timestamp
            FROM audit_log
            WHERE timestamp >= :cutoff
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        target_server_id = _target_key(row["target_server_id"])
        target = grouped.setdefault(
            target_server_id,
            {
                "entries": 0,
                "actions": {},
                "actors": set(),
                "first_at": None,
                "last_at": None,
            },
        )

        action = _action_key(row["action"])
        target["entries"] += 1
        target["actions"][action] = target["actions"].get(action, 0) + 1

        actor = row["actor"]
        if actor is not None:
            target["actors"].add(str(actor))

        timestamp = _as_iso8601(row["timestamp"])
        if target["first_at"] is None or timestamp < target["first_at"]:
            target["first_at"] = timestamp
        if target["last_at"] is None or timestamp > target["last_at"]:
            target["last_at"] = timestamp

    targets: list[dict[str, Any]] = []
    for target_server_id in sorted(grouped):
        target = grouped[target_server_id]
        targets.append(
            {
                "target_server_id": target_server_id,
                "entries": target["entries"],
                "actions": [
                    {"action": action, "count": count}
                    for action, count in sorted(target["actions"].items())
                ],
                "first_at": target["first_at"],
                "last_at": target["last_at"],
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "targets": targets,
        "total_entries": sum(target["entries"] for target in targets),
    }


@router.get("/audit/target-activity", response_model=TargetActivityResponse)
def target_activity(
    window_hours: int = Query(default=24, ge=1, le=8760),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_target_activity(db=db, window_hours=window_hours)


get_audit_target_activity = get_target_activity


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT,
                    target_server_id TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            (now - timedelta(minutes=3), "scan", "alice", "server-a"),
            (now - timedelta(minutes=2), "scan", "alice", "server-a"),
            (now - timedelta(minutes=1), "update", "bob", "server-a"),
            (now - timedelta(minutes=4), "login", "carol", None),
            (now - timedelta(hours=48), "old", "alice", "server-a"),
        ]
        for timestamp, action, actor, target_server_id in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO audit_log (timestamp, action, actor, target_server_id)
                    VALUES (:timestamp, :action, :actor, :target_server_id)
                    """
                ),
                {
                    "timestamp": timestamp,
                    "action": action,
                    "actor": actor,
                    "target_server_id": target_server_id,
                },
            )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    response = TestClient(that_app).get("/api/audit/target-activity")
    assert response.status_code == 200, response.text
    payload = response.json()
    targets = {target["target_server_id"]: target for target in payload["targets"]}
    assert targets["server-a"]["entries"] == 3
    assert targets["unknown"]["entries"] == 1
    assert payload["total_entries"] == 4
    print("PASS")