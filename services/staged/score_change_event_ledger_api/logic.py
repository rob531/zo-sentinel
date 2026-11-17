from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry


_DEFAULT_HOURS = 168
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500


def _cutoff(hours: int) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(tzinfo=None)


def get_score_changes(
    session: Any,
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    hours: int = _DEFAULT_HOURS,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    hours = max(0, int(hours or _DEFAULT_HOURS))
    page = max(1, int(page or 1))
    page_size = min(_MAX_PAGE_SIZE, max(1, int(page_size or _DEFAULT_PAGE_SIZE)))
    offset = (page - 1) * page_size

    params: Dict[str, Any] = {
        "server_id": server_id,
        "axis_name": axis_name,
        "cutoff": _cutoff(hours),
        "limit": page_size,
        "offset": offset,
    }

    aggregate_sql = text(
        """
        SELECT
            e.axis_name AS axis_name,
            COUNT(*) AS count,
            COALESCE(SUM(CASE WHEN e.new_p_top > e.old_p_top THEN 1 ELSE 0 END), 0) AS up,
            COALESCE(SUM(CASE WHEN e.new_p_top < e.old_p_top THEN 1 ELSE 0 END), 0) AS down
        FROM mcp_score_change_events AS e
        INNER JOIN mcp_server_registry AS s
            ON s.server_id = e.server_id
        WHERE e.changed_at >= :cutoff
          AND (:server_id IS NULL OR e.server_id = :server_id)
          AND (:axis_name IS NULL OR e.axis_name = :axis_name)
        GROUP BY e.axis_name
        ORDER BY e.axis_name ASC
        """
    )

    recent_sql = text(
        """
        SELECT
            e.server_id AS server_id,
            s.name AS server_name,
            e.axis_name AS axis_name,
            e.old_label AS old_label,
            e.new_label AS new_label,
            e.old_p_top AS old_p_top,
            e.new_p_top AS new_p_top,
            e.changed_at AS changed_at
        FROM mcp_score_change_events AS e
        INNER JOIN mcp_server_registry AS s
            ON s.server_id = e.server_id
        WHERE e.changed_at >= :cutoff
          AND (:server_id IS NULL OR e.server_id = :server_id)
          AND (:axis_name IS NULL OR e.axis_name = :axis_name)
        ORDER BY e.changed_at DESC, e.id DESC
        LIMIT :limit OFFSET :offset
        """
    )

    total_sql = text(
        """
        SELECT COUNT(*) AS total
        FROM mcp_score_change_events AS e
        INNER JOIN mcp_server_registry AS s
            ON s.server_id = e.server_id
        WHERE e.changed_at >= :cutoff
          AND (:server_id IS NULL OR e.server_id = :server_id)
          AND (:axis_name IS NULL OR e.axis_name = :axis_name)
        """
    )

    by_axis_rows = session.execute(aggregate_sql, params).mappings().all()
    recent_rows = session.execute(recent_sql, params).mappings().all()
    total = int(session.execute(total_sql, params).scalar() or 0)

    by_axis: List[Dict[str, Any]] = [
        {
            "axis_name": row["axis_name"],
            "count": int(row["count"]),
            "up": int(row["up"] or 0),
            "down": int(row["down"] or 0),
        }
        for row in by_axis_rows
    ]

    recent: List[Dict[str, Any]] = [
        {
            "server_id": row["server_id"],
            "server_name": row["server_name"],
            "axis_name": row["axis_name"],
            "old_label": row["old_label"],
            "new_label": row["new_label"],
            "old_p_top": row["old_p_top"],
            "new_p_top": row["new_p_top"],
            "changed_at": row["changed_at"],
        }
        for row in recent_rows
    ]

    return {"total": total, "by_axis": by_axis, "recent": recent}


def get_score_change_events(
    session: Any,
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    hours: int = _DEFAULT_HOURS,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    return get_score_changes(
        session,
        server_id=server_id,
        axis_name=axis_name,
        hours=hours,
        page=page,
        page_size=page_size,
    )


def build_score_change_event_ledger_api_logic(
    session: Any,
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    hours: int = _DEFAULT_HOURS,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    return get_score_changes(
        session,
        server_id=server_id,
        axis_name=axis_name,
        hours=hours,
        page=page,
        page_size=page_size,
    )


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_change_events (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    old_p_top REAL,
                    new_p_top REAL,
                    old_label TEXT,
                    new_label TEXT,
                    changed_at DATETIME NOT NULL,
                    source TEXT
                )
                """
            )
        )
        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES (:server_id, :name, :risk_tier)
                """
            ),
            [
                {"server_id": "srv-a", "name": "Server A", "risk_tier": "high"},
                {"server_id": "srv-b", "name": "Server B", "risk_tier": "medium"},
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_change_events
                    (id, server_id, axis_name, old_p_top, new_p_top,
                     old_label, new_label, changed_at, source)
                VALUES
                    (1, 'srv-a', 'capability', 0.40, 0.65,
                     'Low', 'Medium', :changed_at, 'test'),
                    (2, 'srv-b', 'security', 0.70, 0.55,
                     'High', 'Medium', :changed_at, 'test'),
                    (3, 'srv-a', 'security', 0.30, 0.45,
                     'Low', 'Medium', :changed_at, 'test')
                """
            ),
            {"changed_at": now},
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/scores/changes")
    def score_changes(
        session: Any = Depends(get_session),
        server_id: Optional[str] = Query(default=None),
        axis_name: Optional[str] = Query(default=None),
        hours: int = Query(default=168),
        page: int = Query(default=1),
        page_size: int = Query(default=50),
    ):
        return get_score_changes(
            session,
            server_id=server_id,
            axis_name=axis_name,
            hours=hours,
            page=page,
            page_size=page_size,
        )

    response = TestClient(that_app).get("/api/scores/changes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert {item["axis_name"] for item in payload["by_axis"]} == {
        "capability",
        "security",
    }
    assert any(
        item["axis_name"] == "capability"
        and item["server_id"] == "srv-a"
        and item["old_label"] == "Low"
        and item["new_label"] == "Medium"
        for item in payload["recent"]
    )
    print("PASS")