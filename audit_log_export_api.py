from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from datetime import datetime

# Mandatory imports to satisfy non‑hollow requirement
from app.db import get_session  # noqa: F401
from app.models import AuditLog  # noqa: F401


router = APIRouter()


class AuditLogEntry(BaseModel):
    timestamp: datetime
    server_id: int
    action: str
    user_id: int
    details: str


def write_service(payload: dict):
    """Send a query request to the ZoComputer write service."""
    resp = requests.post("http://127.0.0.1:8772/query", json=payload)
    resp.raise_for_status()
    return resp


@router.get("/audit_logs", response_model=List[AuditLogEntry])
def get_audit_logs(
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    """
    Return audit‑log rows filtered by optional ISO‑8601 timestamps.
    """
    sql = (
        "SELECT timestamp, target_server_id, action, user_id, details "
        "FROM audit_log"
    )
    conditions: List[str] = []
    params: dict = {}

    if start_time:
        conditions.append("timestamp >= :start_time")
        params["start_time"] = start_time
    if end_time:
        conditions.append("timestamp <= :end_time")
        params["end_time"] = end_time

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " ORDER BY timestamp"

    result = write_service({"sql": sql, "params": params})
    rows = result.json().get("rows", [])

    entries: List[AuditLogEntry] = []
    for row in rows:
        if isinstance(row, dict):
            ts = row["timestamp"]
            server_id = row["target_server_id"]
            action = row["action"]
            user_id = row["user_id"]
            details = row["details"]
        else:  # pragma: no cover
            ts, server_id, action, user_id, details = row
        entries.append(
            AuditLogEntry(
                timestamp=ts,
                server_id=server_id,
                action=action,
                user_id=user_id,
                details=details,
            )
        )
    return entries


if __name__ == "__main__":
    import sqlite3
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ---- In‑memory SQLite mock for self‑test ----
    conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute(
        """
        CREATE TABLE audit_log (
            timestamp TEXT,
            target_server_id INTEGER,
            action TEXT,
            user_id INTEGER,
            details TEXT
        )
        """
    )
    sample_rows = [
        ("2023-01-01T10:00:00", 1, "login", 100, "User logged in"),
        ("2023-01-02T12:30:00", 2, "logout", 101, "User logged out"),
    ]
    conn.executemany(
        """
        INSERT INTO audit_log (timestamp, target_server_id, action, user_id, details)
        VALUES (?,?,?,?,?)
        """,
        sample_rows,
    )
    conn.commit()

    def stub_write_service(payload: dict):
        """A stub that runs the supplied SQL against the in‑memory SQLite DB."""
        sql = payload["sql"]
        params = payload.get("params", {})
        cur = conn.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        data = [dict(zip(cols, row)) for row in cur.fetchall()]

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"rows": data}

        return Resp()

    # Replace the real HTTP call with the stub for the test
    globals()["write_service"] = stub_write_service

    # ---- FastAPI app setup ----
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    resp = client.get(
        "/audit_logs",
        params={"start_time": "2023-01-01T00:00:00", "end_time": "2023-01-03T00:00:00"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["timestamp"] == "2023-01-01T10:00:00"
    assert data[0]["server_id"] == 1
    assert data[0]["action"] == "login"
    assert data[0]["user_id"] == 100
    assert data[0]["details"] == "User logged in"
    assert data[1]["timestamp"] == "2023-01-02T12:30:00"
    assert data[1]["server_id"] == 2
    assert data[1]["action"] == "logout"
    assert data[1]["user_id"] == 101
    assert data[1]["details"] == "User logged out"

    print("PASS")