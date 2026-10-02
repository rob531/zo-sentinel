"""
services/staged/sentinel_audit_log/logic.py

Logic for the sentinel_audit_log service.
Provides a single public function `get_audit_log` that queries the
`audit_log` table using the application database session.
"""

from __future__ import annotations

from typing import List, Optional, Dict, Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_audit_log(
    *,
    org_id: int,
    server_id: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """
    Retrieve audit‑log entries filtered by the supplied parameters.

    Parameters
    ----------
    org_id: int
        Organisation identifier – **required**.
    server_id: Optional[int]
        Target server identifier to filter on.
    action: Optional[str]
        Action type to filter on.
    limit: int
        Maximum number of rows to return (default 100).
    offset: int
        Number of rows to skip before returning results (default 0).
    db: Session
        SQLAlchemy session injected by FastAPI.

    Returns
    -------
    List[Dict[str, Any]]
        List of audit‑log records where each record contains:
        ``timestamp, target_server_id, action, actor_id, org_id, detail``.
        ``detail`` is the JSON payload stored in ``detail_json``.
    """
    # Base query
    sql = """
        SELECT
            timestamp,
            target_server_id,
            action,
            actor_id,
            org_id,
            detail_json
        FROM audit_log
        WHERE org_id = :org_id
    """
    params: dict[str, Any] = {"org_id": org_id}

    # Optional filters
    if server_id is not None:
        sql += " AND target_server_id = :server_id"
        params["server_id"] = server_id
    if action is not None:
        sql += " AND action = :action"
        params["action"] = action

    # Ordering & pagination
    sql += """
        ORDER BY timestamp DESC
        LIMIT :limit OFFSET :offset
    """
    params.update({"limit": limit, "offset": offset})

    # Execute the query
    result = db.execute(text(sql), params)

    # Transform rows into the expected dict shape
    rows: List[Dict[str, Any]] = []
    for row in result:
        rows.append(
            {
                "timestamp": row.timestamp,
                "target_server_id": row.target_server_id,
                "action": row.action,
                "actor_id": row.actor_id,
                "org_id": row.org_id,
                "detail": row.detail_json,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test creates an in‑memory SQLite DB, seeds it with three rows
    # spanning two organisations, and validates filtering & pagination.
    from sqlalchemy import create_engine, Table, Column, Integer, String, DateTime, JSON, MetaData
    from datetime import datetime, timedelta

    # ------------------------------------------------------------------- #
    # Setup in‑memory DB and table definition
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    metadata = MetaData()

    audit_log = Table(
        "audit_log",
        metadata,
        Column("timestamp", DateTime, nullable=False),
        Column("target_server_id", Integer, nullable=False),
        Column("action", String, nullable=False),
        Column("actor_id", Integer, nullable=False),
        Column("org_id", Integer, nullable=False),
        Column("detail_json", JSON, nullable=False),
    )
    metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    rows_to_insert = [
        {
            "timestamp": now - timedelta(minutes=5),
            "target_server_id": 1,
            "action": "create",
            "actor_id": 10,
            "org_id": 1,
            "detail_json": {"info": "first"},
        },
        {
            "timestamp": now - timedelta(minutes=3),
            "target_server_id": 2,
            "action": "update",
            "actor_id": 11,
            "org_id": 1,
            "detail_json": {"info": "second"},
        },
        {
            "timestamp": now - timedelta(minutes=1),
            "target_server_id": 3,
            "action": "delete",
            "actor_id": 12,
            "org_id": 2,
            "detail_json": {"info": "third"},
        },
    ]

    with engine.begin() as conn:
        conn.execute(audit_log.insert(), rows_to_insert)

    # ------------------------------------------------------------------- #
    # Helper to invoke get_audit_log with a manual session
    # ------------------------------------------------------------------- #
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, future=True)

    def _call(**kwargs) -> List[Dict[str, Any]]:
        with SessionLocal() as sess:
            return get_audit_log(db=sess, **kwargs)

    # ------------------------------------------------------------------- #
    # Tests
    # ------------------------------------------------------------------- #
    # 1. Org filter – only rows with org_id == 1 should be returned
    org1 = _call(org_id=1, limit=10, offset=0)
    assert len(org1) == 2, f"expected 2 rows for org 1, got {len(org1)}"
    assert all(r["org_id"] == 1 for r in org1)

    # 2. Pagination – limit 1, offset 0 returns the most recent row for org 1
    first_page = _call(org_id=1, limit=1, offset=0)
    assert len(first_page) == 1
    assert first_page[0]["action"] == "update"  # most recent for org 1

    # 3. Pagination – limit 1, offset 1 returns the older row for org 1
    second_page = _call(org_id=1, limit=1, offset=1)
    assert len(second_page) == 1
    assert second_page[0]["action"] == "create"

    # 4. Server filter
    server_filtered = _call(org_id=1, server_id=2, limit=10, offset=0)
    assert len(server_filtered) == 1
    assert server_filtered[0]["target_server_id"] == 2

    # 5. Action filter
    action_filtered = _call(org_id=1, action="create", limit=10, offset=0)
    assert len(action_filtered) == 1
    assert action_filtered[0]["action"] == "create"

    print("PASS")