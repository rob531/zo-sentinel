from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import httpx

from sqlalchemy.orm import Session
from app.db import get_session

router = APIRouter(prefix="/api")


class AuditLogRow(BaseModel):
    timestamp: str
    target_server_id: Optional[str] = None
    action: str
    actor: Optional[str] = None
    detail: Optional[str] = None


class AuditLogResponse(BaseModel):
    rows: List[AuditLogRow]
    total: int


def _query_audit_log(sql: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a query against the write‑service."""
    payload = {"sql": sql, "params": params}
    try:
        resp = httpx.post("http://127.0.0.1:8772/query", json=payload, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/audit/log", response_model=AuditLogResponse)
def get_audit_log(
    server_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),  # kept for contract compatibility
):
    """Return audit log rows with optional filtering and pagination."""
    where_clauses = []
    params: Dict[str, Any] = {}

    if server_id is not None:
        where_clauses.append("server_id = :server_id")
        params["server_id"] = server_id
    if action is not None:
        where_clauses.append("action = :action")
        params["action"] = action

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    sql = (
        "SELECT timestamp, target_server_id, action, actor, detail "
        f"FROM audit_log WHERE {where_sql} "
        "ORDER BY timestamp DESC "
        "LIMIT :limit"
    )
    params["limit"] = limit

    data = _query_audit_log(sql, params)
    rows = data.get("rows", [])
    total = data.get("total", len(rows))

    return {"rows": rows, "total": total}


if __name__ == "__main__":
    # Self‑test using an in‑memory audit store
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory audit log data
    _IN_MEMORY_AUDIT = [
        {
            "timestamp": "2023-01-03T00:00:00Z",
            "target_server_id": "srv1",
            "action": "update",
            "actor": "user",
            "detail": "Updated config",
        },
        {
            "timestamp": "2023-01-02T00:00:00Z",
            "target_server_id": "srv2",
            "action": "delete",
            "actor": "admin",
            "detail": "Deleted server",
        },
        {
            "timestamp": "2023-01-01T00:00:00Z",
            "target_server_id": "srv1",
            "action": "create",
            "actor": "admin",
            "detail": "Created server",
        },
    ]

    class _MockResponse:
        def __init__(self, data: Dict[str, Any]):
            self._data = data

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Dict[str, Any]:
            return self._data

    def _mock_post(url: str, json: Dict[str, Any], timeout: float) -> _MockResponse:
        sql = json.get("sql", "")
        params = json.get("params", {})
        limit = params.get("limit", 50)

        # Simple filter based on supplied params (ignore actual SQL parsing)
        filtered = _IN_MEMORY_AUDIT
        if "server_id" in params:
            filtered = [r for r in filtered if r["target_server_id"] == params["server_id"]]
        if "action" in params:
            filtered = [r for r in filtered if r["action"] == params["action"]]

        # Order by timestamp descending (lexicographic works for ISO strings)
        filtered.sort(key=lambda r: r["timestamp"], reverse=True)

        rows = filtered[:limit]
        return _MockResponse({"rows": rows, "total": len(filtered)})

    # Patch httpx.post with our mock
    httpx.post = _mock_post  # type: ignore

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # 1. Default limit (50) – should return all 3 rows
    resp = client.get("/api/audit/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["rows"]) == 3

    # 2. Limit parameter
    resp = client.get("/api/audit/log?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rows"]) == 2

    # 3. Server filter
    resp = client.get("/api/audit/log?server_id=srv2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["rows"][0]["target_server_id"] == "srv2"

    print("PASS")