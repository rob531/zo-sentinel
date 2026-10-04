from typing import List, Optional, Dict, Any

import requests
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session


def _query_audit_log(sql: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a parameterised query against the write‑service bus."""
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


class AuditLogRow(BaseModel):
    id: int
    timestamp: str
    action: str
    target_server_id: Optional[int]
    target_user_id: Optional[int]
    actor_email: Optional[str]
    detail: Optional[Dict[str, Any]]
    source_ip: Optional[str]


class AuditLogResponse(BaseModel):
    total: int
    rows: List[AuditLogRow]


def get_audit_log(
    limit: int = 50,
    offset: int = 0,
    target_server_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    action: Optional[str] = None,
    from_: Optional[str] = None,
    to: Optional[str] = None,
    session: Session = Depends(get_session),  # noqa: ARG001 – kept for FastAPI compatibility
) -> AuditLogResponse:
    """Return audit‑log entries with optional filtering."""
    where_clauses: List[str] = []
    params: Dict[str, Any] = {}

    if target_server_id is not None:
        where_clauses.append("target_server_id = :target_server_id")
        params["target_server_id"] = target_server_id
    if target_user_id is not None:
        where_clauses.append("target_user_id = :target_user_id")
        params["target_user_id"] = target_user_id
    if action:
        where_clauses.append("action = :action")
        params["action"] = action
    if from_:
        where_clauses.append("timestamp >= :from_")
        params["from_"] = from_
    if to:
        where_clauses.append("timestamp <= :to")
        params["to"] = to

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql = f"""
        SELECT
            id,
            timestamp,
            action,
            target_server_id,
            target_user_id,
            actor_email,
            detail,
            source_ip
        FROM audit_log
        {where_sql}
        ORDER BY timestamp DESC
        LIMIT :limit OFFSET :offset
    """

    params.update({"limit": limit, "offset": offset})

    raw = _query_audit_log(sql, params)
    rows = [AuditLogRow(**row) for row in raw.get("rows", [])]
    total = raw.get("total", len(rows))

    return AuditLogResponse(total=total, rows=rows)


if __name__ == "__main__":
    # Self‑test without external network calls.
    def _mock_query_audit_log(sql: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "total": 3,
            "rows": [
                {
                    "id": 1,
                    "timestamp": "2023-01-01T00:00:00Z",
                    "action": "create",
                    "target_server_id": 10,
                    "target_user_id": 20,
                    "actor_email": "admin@example.com",
                    "detail": {"info": "test"},
                    "source_ip": "1.2.3.4",
                },
                {
                    "id": 2,
                    "timestamp": "2023-01-02T00:00:00Z",
                    "action": "update",
                    "target_server_id": 10,
                    "target_user_id": 21,
                    "actor_email": "user@example.com",
                    "detail": {"info": "test2"},
                    "source_ip": "1.2.3.5",
                },
                {
                    "id": 3,
                    "timestamp": "2023-01-03T00:00:00Z",
                    "action": "delete",
                    "target_server_id": 11,
                    "target_user_id": 22,
                    "actor_email": "other@example.com",
                    "detail": {"info": "test3"},
                    "source_ip": "1.2.3.6",
                },
            ],
        }

    # Patch the network call.
    _original_query = _query_audit_log
    globals()["_query_audit_log"] = _mock_query_audit_log

    response = get_audit_log(limit=10, offset=0)

    assert response.total >= 3
    first = response.rows[0]
    for field in [
        "id",
        "timestamp",
        "action",
        "target_server_id",
        "target_user_id",
        "actor_email",
        "detail",
        "source_ip",
    ]:
        assert getattr(first, field) is not None

    print("PASS")

    # Restore original function (good practice, though not required here).
    globals()["_query_audit_log"] = _original_query