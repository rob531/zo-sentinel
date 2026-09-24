from datetime import datetime, timedelta
from typing import Literal

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class DateRange(BaseModel):
    start: datetime
    end: datetime


class ByTypeEntry(BaseModel):
    change_type: str
    count: int


class Summary(BaseModel):
    total_changes: int
    by_type: dict[str, int]


class ByDayEntry(BaseModel):
    date: str
    change_type: str
    old_tier: str | None
    new_tier: str | None
    server_id: str
    server_name: str | None


class TierChangesResponse(BaseModel):
    perspective_id: str
    date_range: DateRange
    summary: Summary
    by_day: list[ByDayEntry]
    unseen_count: int


def get_tier_changes(
    perspective_id: str,
    seen: bool | None,
    db: Session,
) -> TierChangesResponse:
    persp = db.execute(select(Perspective).where(Perspective.id == perspective_id)).scalar_one_or_none()
    if not persp:
        raise HTTPException(status_code=404, detail="Perspective not found")

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    query = (
        select(
            PerspectiveEvent,
            McpServerRegistry.name.label("server_name"),
        )
        .outerjoin(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.server_id,
        )
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .where(PerspectiveEvent.created_at >= start_date)
        .where(PerspectiveEvent.created_at <= end_date)
    )

    if seen is not None:
        query = query.where(PerspectiveEvent.seen == (1 if seen else 0))

    rows = db.execute(query.order_by(PerspectiveEvent.created_at)).all()

    by_type: dict[str, int] = {}
    by_day: list[ByDayEntry] = []
    unseen_count = 0

    for row in rows:
        event: PerspectiveEvent = row[0]
        server_name: str | None = row[1]

        change_type = event.change_type
        by_type[change_type] = by_type.get(change_type, 0) + 1

        by_day.append(
            ByDayEntry(
                date=event.created_at.strftime("%Y-%m-%d"),
                change_type=change_type,
                old_tier=event.old_tier,
                new_tier=event.new_tier,
                server_id=event.server_id,
                server_name=server_name,
            )
        )

        if not event.seen:
            unseen_count += 1

    return TierChangesResponse(
        perspective_id=perspective_id,
        date_range=DateRange(start=start_date, end=end_date),
        summary=Summary(total_changes=len(rows), by_type=by_type),
        by_day=by_day,
        unseen_count=unseen_count,
    )


if __name__ == "__main__":
    import sqlite3

    from fastapi.testclient import TestClient
    from sqlalchemy import StaticPool, create_engine
    from sqlalchemy.orm import sessionmaker

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE perspectives (id TEXT PRIMARY KEY, org_id TEXT, name TEXT, description TEXT, created_by TEXT, created_at TEXT, updated_at TEXT, facet_filters TEXT)"
    )
    conn.execute(
        "CREATE TABLE perspective_events (id INTEGER PRIMARY KEY, perspective_id TEXT, server_id TEXT, change_type TEXT, old_tier TEXT, new_tier TEXT, seen INTEGER, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE mcp_server_registry (server_id TEXT PRIMARY KEY, name TEXT, url TEXT, description TEXT, registry_source TEXT, risk_tier TEXT, trust_score REAL, confidence REAL, verdict TEXT, verdict_reasoning TEXT, first_seen TEXT, last_seen TEXT, last_scanned TEXT, last_assessed TEXT, scan_count INTEGER, meta TEXT)"
    )
    conn.commit()

    now = datetime.utcnow()
    day1 = now - timedelta(days=2)
    day2 = now - timedelta(days=1)

    conn.execute("INSERT INTO perspectives VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("p1", "o1", "Test Perspective", "desc", "u1", now.isoformat(), now.isoformat(), "{}"))
    conn.execute("INSERT INTO mcp_server_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("s1", "Server One", "http://s1", "desc", "src", "medium", 0.7, 0.8, "ok", "good", now.isoformat(), now.isoformat(), now.isoformat(), now.isoformat(), 5, "{}"))
    conn.execute("INSERT INTO mcp_server_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("s2", "Server Two", "http://s2", "desc2", "src", "high", 0.6, 0.7, "warn", "fair", now.isoformat(), now.isoformat(), now.isoformat(), now.isoformat(), 3, "{}"))
    conn.execute("INSERT INTO perspective_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (1, "p1", "s1", "server_upgrade", "low", "high", 1, day1.isoformat()))
    conn.execute("INSERT INTO perspective_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (2, "p1", "s2", "server_downgrade", "high", "low", 0, day2.isoformat()))
    conn.execute("INSERT INTO perspective_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (3, "p1", "s1", "new_server", None, "medium", 1, day1.isoformat()))
    conn.commit()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    in_mem_conn = engine.connect()
    in_mem_conn.exec_driver_sql("CREATE TABLE perspectives (id TEXT PRIMARY KEY, org_id TEXT, name TEXT, description TEXT, created_by TEXT, created_at TEXT, updated_at TEXT, facet_filters TEXT)")
    in_mem_conn.exec_driver_sql("CREATE TABLE perspective_events (id INTEGER PRIMARY KEY, perspective_id TEXT, server_id TEXT, change_type TEXT, old_tier TEXT, new_tier TEXT, seen INTEGER, created_at TEXT)")
    in_mem_conn.exec_driver_sql("CREATE TABLE mcp_server_registry (server_id TEXT PRIMARY KEY, name TEXT, url TEXT, description TEXT, registry_source TEXT, risk_tier TEXT, trust_score REAL, confidence REAL, verdict TEXT, verdict_reasoning TEXT, first_seen TEXT, last_seen TEXT, last_scanned TEXT, last_assessed TEXT, scan_count INTEGER, meta TEXT)")
    in_mem_conn.exec_driver_sql("INSERT INTO perspectives VALUES ('p1', 'o1', 'Test Perspective', 'desc', 'u1', :now, :now, '{}')", {"now": now.isoformat()})
    in_mem_conn.exec_driver_sql("INSERT INTO mcp_server_registry VALUES ('s1', 'Server One', 'http://s1', 'desc', 'src', 'medium', 0.7, 0.8, 'ok', 'good', :now, :now, :now, :now, 5, '{}')", {"now": now.isoformat()})
    in_mem_conn.exec_driver_sql("INSERT INTO mcp_server_registry VALUES ('s2', 'Server Two', 'http://s2', 'desc2', 'src', 'high', 0.6, 0.7, 'warn', 'fair', :now, :now, :now, :now, 3, '{}')", {"now": now.isoformat()})
    in_mem_conn.exec_driver_sql("INSERT INTO perspective_events VALUES (1, 'p1', 's1', 'server_upgrade', 'low', 'high', 1, :d)", {"d": day1.isoformat()})
    in_mem_conn.exec_driver_sql("INSERT INTO perspective_events VALUES (2, 'p1', 's2', 'server_downgrade', 'high', 'low', 0, :d)", {"d": day2.isoformat()})
    in_mem_conn.exec_driver_sql("INSERT INTO perspective_events VALUES (3, 'p1', 's1', 'new_server', NULL, 'medium', 1, :d)", {"d": day1.isoformat()})
    in_mem_conn.commit()

    TestingSessionLocal = sessionmaker(bind=engine)

    app = FastAPI()

    @app.get("/api/perspectives/{perspective_id}/tier-changes")
    def api_tier_changes(
        perspective_id: str,
        seen: bool | None = Query(default=None),
    ) -> TierChangesResponse:
        db = TestingSessionLocal()
        try:
            return get_tier_changes(perspective_id, seen, db)
        finally:
            db.close()

    client = TestClient(app)

    resp = client.get("/api/perspectives/p1/tier-changes")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["perspective_id"] == "p1"
    assert data["summary"]["total_changes"] >= 3, f"Expected >= 3, got {data['summary']['total_changes']}"
    assert "server_upgrade" in data["summary"]["by_type"]
    assert "server_downgrade" in data["summary"]["by_type"]
    assert "new_server" in data["summary"]["by_type"]

    resp_seen = client.get("/api/perspectives/p1/tier-changes", params={"seen": "true"})
    assert resp_seen.status_code == 200
    data_seen = resp_seen.json()
    assert data_seen["summary"]["total_changes"] == 2

    resp_unseen = client.get("/api/perspectives/p1/tier-changes", params={"seen": "false"})
    assert resp_unseen.status_code == 200
    data_unseen = resp_unseen.json()
    assert data_unseen["summary"]["total_changes"] == 1
    assert data_unseen["unseen_count"] == 1

    resp_404 = client.get("/api/perspectives/nonexistent/tier-changes")
    assert resp_404.status_code == 404

    print("PASS")