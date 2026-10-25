from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

try:
    from .logic import get_recent_audit_entries as _get_recent_audit_entries
except ImportError:
    from .logic import get_recent_audit_entries_logic as _get_recent_audit_entries

router = APIRouter(prefix="/api")


class AuditLogEntryResponse(BaseModel):
    timestamp: datetime
    target_server_id: Optional[Union[str, int]] = None
    server_name: Optional[str] = None
    actor: Optional[str] = None
    action: Optional[str] = None
    detail: Any = None


class RecentAuditEntriesResponse(BaseModel):
    count: int
    entries: List[AuditLogEntryResponse]


def _call_logic(
    session: Session, limit: int, target_server_id: Optional[str]
) -> Any:
    parameters = inspect.signature(_get_recent_audit_entries).parameters
    kwargs: Dict[str, Any] = {}

    for name in parameters:
        if name in {"session", "db_session", "db"}:
            kwargs[name] = session
        elif name == "limit":
            kwargs[name] = limit
        elif name in {"server_id", "target_server_id", "server_filter"}:
            kwargs[name] = target_server_id

    result = _get_recent_audit_entries(**kwargs)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


def _normalize_entries(result: Any) -> List[Dict[str, Any]]:
    entries: Any = result
    if isinstance(result, dict):
        entries = result.get("entries", [])
    elif isinstance(result, tuple) and len(result) == 2:
        entries = result[1]

    normalized: List[Dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, BaseModel):
            entry = entry.model_dump() if hasattr(entry, "model_dump") else entry.dict()
        elif not isinstance(entry, dict):
            entry = {
                key: getattr(entry, key)
                for key in (
                    "timestamp",
                    "target_server_id",
                    "server_name",
                    "actor",
                    "action",
                    "detail",
                    "meta",
                )
                if hasattr(entry, key)
            }

        detail = entry.get("detail")
        if detail is None:
            detail = entry.get("meta")
        if detail is None:
            detail = entry.get("details")

        normalized.append(
            {
                "timestamp": entry.get("timestamp"),
                "target_server_id": entry.get("target_server_id"),
                "server_name": entry.get("server_name"),
                "actor": entry.get("actor"),
                "action": entry.get("action"),
                "detail": detail,
            }
        )
    return normalized


@router.get(
    "/audit-log/recent",
    response_model=RecentAuditEntriesResponse,
    summary="Return the most recent audit-log entries",
)
def get_recent_audit_entries(
    limit: int = Query(default=50, ge=1, le=1000),
    server_id: Optional[str] = Query(default=None),
    target_server_id: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> RecentAuditEntriesResponse:
    selected_server_id = server_id if server_id is not None else target_server_id
    entries = _normalize_entries(
        _call_logic(session=session, limit=limit, target_server_id=selected_server_id)
    )
    return RecentAuditEntriesResponse(count=len(entries), entries=entries)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY,
                    timestamp DATETIME NOT NULL,
                    target_server_id TEXT,
                    actor TEXT,
                    action TEXT,
                    detail TEXT,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    confidence FLOAT,
                    description TEXT,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    server_id TEXT,
                    trust_score FLOAT,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, description, meta)
                VALUES
                    (:server_id, :name, :description, :meta)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Primary Server",
                    "description": "Primary test server",
                    "meta": "{}",
                },
                {
                    "server_id": "server-2",
                    "name": "Secondary Server",
                    "description": "Secondary test server",
                    "meta": "{}",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO audit_log
                    (id, timestamp, target_server_id, actor, action, detail, meta)
                VALUES
                    (:id, :timestamp, :target_server_id, :actor, :action, :detail, :meta)
                """
            ),
            [
                {
                    "id": 1,
                    "timestamp": "2025-01-01 10:00:00",
                    "target_server_id": "server-1",
                    "actor": "tester",
                    "action": "scan",
                    "detail": "oldest",
                    "meta": "oldest",
                },
                {
                    "id": 2,
                    "timestamp": "2025-01-02 10:00:00",
                    "target_server_id": "server-2",
                    "actor": "tester",
                    "action": "assess",
                    "detail": "middle",
                    "meta": "middle",
                },
                {
                    "id": 3,
                    "timestamp": "2025-01-03 10:00:00",
                    "target_server_id": "server-1",
                    "actor": "tester",
                    "action": "update",
                    "detail": "newest",
                    "meta": "newest",
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/audit-log/recent", params={"limit": 10})
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["entries"]) == 3
        assert payload["entries"][0]["action"] == "update"
        assert payload["entries"][0]["server_name"] == "Primary Server"

    print("PASS")