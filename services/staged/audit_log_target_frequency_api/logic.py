from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def _validated_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def _since_timestamp(since_hours: int | None) -> datetime | None:
    if since_hours is None:
        return None
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=max(0, int(since_hours))
    )


def _action_column(db: Session) -> str:
    columns = {
        column["name"]
        for column in inspect(db.get_bind()).get_columns("audit_log")
    }
    for candidate in ("action", "event_type", "event"):
        if candidate in columns:
            return candidate
    raise RuntimeError("audit_log has no action or event column")


def build_query(limit: int = DEFAULT_LIMIT, since_hours: int | None = None) -> str:
    query = """
        SELECT
            target_server_id,
            COUNT(*) AS event_count,
            MAX(timestamp) AS last_seen
        FROM audit_log
        WHERE target_server_id IS NOT NULL
    """
    if since_hours is not None:
        query += " AND timestamp >= :since_timestamp"
    query += """
        GROUP BY target_server_id
        ORDER BY event_count DESC, last_seen DESC, target_server_id ASC
        LIMIT :limit
    """
    return query


def _build_query(limit: int = DEFAULT_LIMIT, since_hours: int | None = None) -> str:
    return build_query(limit=limit, since_hours=since_hours)


def get_target_frequency(
    db: Session = Depends(get_session),
    limit: int = DEFAULT_LIMIT,
    since_hours: int | None = None,
) -> dict[str, Any]:
    limit = _validated_limit(limit)
    since_timestamp = _since_timestamp(since_hours)
    action_column = _action_column(db)
    quote = db.get_bind().dialect.identifier_preparer.quote

    params: dict[str, Any] = {"limit": limit}
    where_clause = ""
    if since_timestamp is not None:
        where_clause = " WHERE timestamp >= :since_timestamp"
        params["since_timestamp"] = since_timestamp

    total_query = text(
        "SELECT COUNT(*) FROM audit_log" + where_clause
    )
    total_events = int(db.execute(total_query, params).scalar_one())

    target_query = text(
        """
        SELECT
            target_server_id,
            COUNT(*) AS event_count,
            MAX(timestamp) AS last_seen
        FROM audit_log
        WHERE target_server_id IS NOT NULL
        """
        + (
            " AND timestamp >= :since_timestamp"
            if since_timestamp is not None
            else ""
        )
        + """
        GROUP BY target_server_id
        ORDER BY event_count DESC, last_seen DESC, target_server_id ASC
        LIMIT :limit
        """
    )
    target_rows = db.execute(target_query, params).mappings().all()

    action_query = text(
        f"""
        SELECT
            target_server_id,
            {quote(action_column)} AS action,
            COUNT(*) AS count
        FROM audit_log
        WHERE target_server_id IS NOT NULL
        """
        + (
            " AND timestamp >= :since_timestamp"
            if since_timestamp is not None
            else ""
        )
        + f"""
          AND {quote(action_column)} IS NOT NULL
        GROUP BY target_server_id, {quote(action_column)}
        ORDER BY target_server_id, count DESC, {quote(action_column)} ASC
        """
    )
    action_rows = db.execute(action_query, params).mappings().all()

    action_counts: dict[Any, list[dict[str, Any]]] = {}
    for row in action_rows:
        action_counts.setdefault(row["target_server_id"], []).append(
            {
                "action": row["action"],
                "count": int(row["count"]),
            }
        )

    targets = [
        {
            "target_server_id": row["target_server_id"],
            "event_count": int(row["event_count"]),
            "last_seen": row["last_seen"],
            "actions": action_counts.get(row["target_server_id"], []),
        }
        for row in target_rows
    ]

    return {
        "targets": targets,
        "total_events": total_events,
        "limit": limit,
    }


def get_audit_targets(
    db: Session = Depends(get_session),
    limit: int = DEFAULT_LIMIT,
    since_hours: int | None = None,
) -> dict[str, Any]:
    return get_target_frequency(
        db=db,
        limit=limit,
        since_hours=since_hours,
    )


def get_audit_target_frequency(
    db: Session = Depends(get_session),
    limit: int = DEFAULT_LIMIT,
    since_hours: int | None = None,
) -> dict[str, Any]:
    return get_target_frequency(
        db=db,
        limit=limit,
        since_hours=since_hours,
    )


def get_targets(
    db: Session = Depends(get_session),
    limit: int = DEFAULT_LIMIT,
    since_hours: int | None = None,
) -> dict[str, Any]:
    return get_target_frequency(
        db=db,
        limit=limit,
        since_hours=since_hours,
    )


def logic(
    db: Session = Depends(get_session),
    limit: int = DEFAULT_LIMIT,
    since_hours: int | None = None,
) -> dict[str, Any]:
    return get_target_frequency(
        db=db,
        limit=limit,
        since_hours=since_hours,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    audit_log = Table(
        "audit_log",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("timestamp", DateTime, nullable=False),
        Column("target_server_id", String(100), nullable=False),
        Column("action", String(100), nullable=False),
        Column("event_type", String(100), nullable=False),
    )
    metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    with TestSession() as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            (now - timedelta(minutes=5), "server-a", "scan", "scan_started"),
            (now - timedelta(minutes=4), "server-a", "scan", "scan_completed"),
            (now - timedelta(minutes=3), "server-a", "update", "update_completed"),
            (now - timedelta(minutes=2), "server-b", "scan", "scan_started"),
            (now - timedelta(minutes=1), "server-b", "delete", "delete_completed"),
        ]
        session.execute(
            audit_log.insert(),
            [
                {
                    "timestamp": timestamp,
                    "target_server_id": target_server_id,
                    "action": action,
                    "event_type": event_type,
                }
                for timestamp, target_server_id, action, event_type in rows
            ],
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/audit/targets")
    def test_targets(
        limit: int = DEFAULT_LIMIT,
        since_hours: int | None = None,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_target_frequency(
            db=db,
            limit=limit,
            since_hours=since_hours,
        )

    def override_get_session() -> Session:
        return TestSession()

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/audit/targets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_events"] == 5
    assert payload["targets"][0]["event_count"] == 3
    assert len(payload["targets"]) == 2
    assert all(entry["last_seen"] for entry in payload["targets"])
    print("PASS")