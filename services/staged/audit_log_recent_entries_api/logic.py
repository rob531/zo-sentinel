from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_DETAIL_QUERIES = {
    "detail": """
        SELECT
            timestamp,
            target_server_id,
            actor,
            action,
            detail AS detail
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT :limit
    """,
    "meta": """
        SELECT
            timestamp,
            target_server_id,
            actor,
            action,
            meta AS detail
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT :limit
    """,
    "metadata": """
        SELECT
            timestamp,
            target_server_id,
            actor,
            action,
            metadata AS detail
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT :limit
    """,
}


def _get_detail_column(session: Session) -> str:
    try:
        columns = {
            column["name"]
            for column in inspect(session.get_bind()).get_columns("audit_log")
        }
    except Exception:
        columns = set()

    for candidate in ("detail", "meta", "metadata"):
        if candidate in columns:
            return candidate

    raise RuntimeError("audit_log has no supported detail column")


def get_recent_audit_entries(
    session: Session = Depends(get_session),
    limit: int = 50,
    server_id: str | None = None,
    target_server_id: str | None = None,
) -> list[dict[str, Any]]:
    if server_id is not None and target_server_id is not None and server_id != target_server_id:
        raise ValueError("server_id and target_server_id must not conflict")

    selected_server_id = server_id if server_id is not None else target_server_id
    bounded_limit = max(1, min(int(limit), 500))
    detail_column = _get_detail_column(session)

    params: dict[str, Any] = {"limit": bounded_limit}
    query = _DETAIL_QUERIES[detail_column]

    if selected_server_id is not None:
        query = query.replace(
            "ORDER BY timestamp DESC",
            "WHERE target_server_id = :target_server_id\n        ORDER BY timestamp DESC",
        )
        params["target_server_id"] = selected_server_id

    audit_rows = session.execute(text(query), params).mappings().all()
    registry_rows = session.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name)
    ).all()

    server_names: dict[str, str | None] = {}
    for row in registry_rows:
        server_names.setdefault(row.server_id, row.name)

    entries: list[dict[str, Any]] = []
    for row in audit_rows:
        entry = dict(row)
        target = entry.get("target_server_id")
        entry["server_name"] = server_names.get(target)
        entries.append(
            {
                "timestamp": entry.get("timestamp"),
                "target_server_id": target,
                "server_name": entry.get("server_name"),
                "actor": entry.get("actor"),
                "action": entry.get("action"),
                "detail": entry.get("detail"),
            }
        )

    return entries


def get_recent_entries(
    session: Session = Depends(get_session),
    limit: int = 50,
    server_id: str | None = None,
    target_server_id: str | None = None,
) -> list[dict[str, Any]]:
    return get_recent_audit_entries(
        session=session,
        limit=limit,
        server_id=server_id,
        target_server_id=target_server_id,
    )


def list_recent_audit_entries(
    session: Session = Depends(get_session),
    limit: int = 50,
    server_id: str | None = None,
    target_server_id: str | None = None,
) -> list[dict[str, Any]]:
    return get_recent_audit_entries(
        session=session,
        limit=limit,
        server_id=server_id,
        target_server_id=target_server_id,
    )


def get_recent_audit_log_entries(
    session: Session = Depends(get_session),
    limit: int = 50,
    server_id: str | None = None,
    target_server_id: str | None = None,
) -> list[dict[str, Any]]:
    return get_recent_audit_entries(
        session=session,
        limit=limit,
        server_id=server_id,
        target_server_id=target_server_id,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    target_server_id TEXT,
                    actor TEXT,
                    action TEXT,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry (server_id, name) VALUES "
                "(:server_id, :name)"
            ),
            [
                {"server_id": "server-a", "name": "Alpha Server"},
                {"server_id": "server-b", "name": "Beta Server"},
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO audit_log
                    (id, timestamp, target_server_id, actor, action, meta)
                VALUES
                    (:id, :timestamp, :target_server_id, :actor, :action, :meta)
                """
            ),
            [
                {
                    "id": 1,
                    "timestamp": "2025-01-01T10:00:00Z",
                    "target_server_id": "server-a",
                    "actor": "alice",
                    "action": "scan",
                    "meta": "oldest",
                },
                {
                    "id": 2,
                    "timestamp": "2025-01-03T10:00:00Z",
                    "target_server_id": "server-b",
                    "actor": "carol",
                    "action": "update",
                    "meta": "newest",
                },
                {
                    "id": 3,
                    "timestamp": "2025-01-02T10:00:00Z",
                    "target_server_id": "server-a",
                    "actor": "bob",
                    "action": "review",
                    "meta": "middle",
                },
            ],
        )

    db_session = Session(engine)

    def override_get_session():
        yield db_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/audit-log/recent")
    def recent_entries(
        session: Session = Depends(get_session),
        limit: int = 50,
        server_id: str | None = None,
    ):
        entries = get_recent_audit_entries(
            session=session,
            limit=limit,
            server_id=server_id,
        )
        return {"count": len(entries), "entries": entries}

    with TestClient(that_app) as client:
        response = client.get("/api/audit-log/recent")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["entries"]) == 3
        assert payload["entries"][0]["action"] == "update"
        assert payload["entries"][0]["server_name"] == "Beta Server"

    db_session.close()
    engine.dispose()
    print("PASS")