# deps: fastapi, pydantic, sqlalchemy
"""verdict_change_audit -- audit trail of server risk-tier and verdict changes.

GET /api/verdict-changes/audit
  Returns recent tier/verdict change events across all servers:
    - total count of recent changes
    - breakdown by change_type
    - paginated list of recent events with server names

GET /api/verdict-changes/audit/{server_id}
  Returns the full change-history for a specific server (most recent first).

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["verdict_change_audit"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class VerdictChangeEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: str
    server_name: Optional[str] = None
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


class ChangeTypeBreakdown(BaseModel):
    tier_change: int = 0
    verdict_change: int = 0
    perspective_change: int = 0
    other: int = 0


class VerdictChangeAuditResponse(BaseModel):
    total: int
    by_change_type: ChangeTypeBreakdown
    recent: List[VerdictChangeEntry]


class ServerVerdictChangeHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


class ServerVerdictChangeHistoryResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    events: List[ServerVerdictChangeHistoryEntry]
    total: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/verdict-changes/audit", response_model=VerdictChangeAuditResponse)
def get_verdict_change_audit(
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    change_type: Optional[str] = Query(default=None, description="Filter by change_type"),
    limit: int = Query(default=50, ge=1, le=200, description="Max recent events to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_session),
) -> VerdictChangeAuditResponse:
    """
    Audit summary of recent risk-tier / verdict change events across the registry.
    Returns total count, breakdown by change_type, and recent event entries.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days)

    # Total count (unfiltered by change_type, only by time)
    total = (
        db.query(func.count(PerspectiveEvent.id))
        .filter(PerspectiveEvent.created_at >= cutoff)
        .scalar()
        or 0
    )

    # Per-change-type counts
    type_counts_raw = (
        db.query(
            PerspectiveEvent.change_type,
            func.count(PerspectiveEvent.id).label("cnt"),
        )
        .filter(PerspectiveEvent.created_at >= cutoff)
        .group_by(PerspectiveEvent.change_type)
        .all()
    )

    breakdown = ChangeTypeBreakdown()
    for ct, cnt in type_counts_raw:
        if ct == "tier_change":
            breakdown.tier_change = cnt
        elif ct == "verdict_change":
            breakdown.verdict_change = cnt
        elif ct == "perspective_change":
            breakdown.perspective_change = cnt
        else:
            breakdown.other += cnt

    # Recent events with server names (LEFT JOIN)
    stmt = (
        select(
            PerspectiveEvent,
            McpServerRegistry.name.label("server_name"),
        )
        .outerjoin(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.server_id,
        )
        .filter(PerspectiveEvent.created_at >= cutoff)
    )

    if change_type:
        stmt = stmt.filter(PerspectiveEvent.change_type == change_type)

    stmt = stmt.order_by(PerspectiveEvent.created_at.desc()).offset(offset).limit(limit)

    rows = db.execute(stmt).all()

    recent = []
    for row in rows:
        event = row[0]
        server_name = row[1]
        entry = VerdictChangeEntry.model_validate(event)
        entry.server_name = server_name
        recent.append(entry)

    return VerdictChangeAuditResponse(
        total=total,
        by_change_type=breakdown,
        recent=recent,
    )


@router.get("/verdict-changes/audit/{server_id}", response_model=ServerVerdictChangeHistoryResponse)
def get_server_verdict_change_history(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVerdictChangeHistoryResponse:
    """
    Full change-history for a single server (most recent events first).
    Returns 404 if the server is not in the registry.
    """
    # Verify server exists
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{server_id}' not found in registry",
        )

    rows = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .all()
    )

    events = [ServerVerdictChangeHistoryEntry.model_validate(r) for r in rows]

    return ServerVerdictChangeHistoryResponse(
        server_id=server_id,
        server_name=server.name,
        events=events,
        total=len(events),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    # Stub app modules so top-level imports resolve at test time
    mock_db = MagicMock()
    mock_db.get_session = MagicMock()
    mock_models = MagicMock()
    mock_models.McpServerRegistry = MagicMock()
    mock_models.PerspectiveEvent = MagicMock()
    mock_models.Base = MagicMock()
    sys.modules["app"] = MagicMock()
    sys.modules["app.db"] = mock_db
    sys.modules["app.models"] = mock_models

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from sqlalchemy.orm import declarative_base
    Base = declarative_base()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(256),
                risk_tier VARCHAR(32)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS perspective_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perspective_id VARCHAR(64),
                server_id VARCHAR(128),
                change_type VARCHAR(32),
                old_tier VARCHAR(32),
                new_tier VARCHAR(32),
                seen INTEGER DEFAULT 0,
                created_at TIMESTAMP
            )
        """))
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    now = datetime.utcnow()
    with SessionLocal() as sess:
        # Seed servers
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES (:sid, :name, :tier)"),
            {"sid": "srv_x", "name": "Server Alpha", "tier": "medium"},
        )
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES (:sid, :name, :tier)"),
            {"sid": "srv_y", "name": "Server Beta", "tier": "high"},
        )
        # Seed perspective events
        events = [
            ("srv_x", "tier_change",  "low",    "medium", now.replace(hour=12) - 2 * 86400),
            ("srv_x", "tier_change",  "medium", "high",   now.replace(hour=12) - 1 * 86400),
            ("srv_y", "verdict_change", "low",  "high",   now.replace(hour=10) - 3 * 86400),
        ]
        for i, (sid, ct, old_t, new_t, created) in enumerate(events, start=1):
            sess.execute(
                text("""
                    INSERT INTO perspective_events
                        (perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
                    VALUES (:pid, :sid, :ct, :old, :new, :seen, :ca)
                """),
                {
                    "pid":  "persp_1",
                    "sid":  sid,
                    "ct":   ct,
                    "old":  old_t,
                    "new":  new_t,
                    "seen": 1 if i % 2 == 0 else 0,
                    "ca":   created.isoformat(),
                },
            )
        sess.commit()

    client = TestClient(app)

    # -- Test 1: audit summary with default window (30 days) --
    resp = client.get("/api/verdict-changes/audit")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["total"] == 3, f"expected total=3, got {data['total']}"
    assert data["by_change_type"]["tier_change"] == 2, f"tier_change={data['by_change_type']}"
    assert data["by_change_type"]["verdict_change"] == 1, f"verdict_change={data['by_change_type']}"
    assert len(data["recent"]) == 3, f"expected 3 recent, got {len(data['recent'])}"

    # Verify server_name join worked
    names = {e["server_name"] for e in data["recent"]}
    assert "Server Alpha" in names and "Server Beta" in names, f"names={names}"

    # Verify ordering (most recent first)
    createds = [e["created_at"] for e in data["recent"]]
    assert createds == sorted(createds, reverse=True), "events not sorted desc by created_at"

    # -- Test 2: pagination --
    resp2 = client.get("/api/verdict-changes/audit?offset=1&limit=1")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 3
    assert len(data2["recent"]) == 1

    # -- Test 3: filter by change_type --
    resp3 = client.get("/api/verdict-changes/audit?change_type=tier_change")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["total"] == 2, f"expected 2, got {data3['total']}"
    for e in data3["recent"]:
        assert e["change_type"] == "tier_change", f"wrong change_type: {e['change_type']}"

    # -- Test 4: per-server history --
    resp4 = client.get("/api/verdict-changes/audit/srv_x")
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["server_id"] == "srv_x"
    assert data4["server_name"] == "Server Alpha"
    assert data4["total"] == 2
    assert all(e["server_id"] == "srv_x" for e in data4["events"])

    # Most recent event should be first (tier_change low->medium was 2 days ago;
    #  medium->high was 1 day ago)
    assert data4["events"][0]["new_tier"] == "high"
    assert data4["events"][1]["new_tier"] == "medium"

    # -- Test 5: 404 for unknown server --
    resp5 = client.get("/api/verdict-changes/audit/unknown_srv")
    assert resp5.status_code == 404, f"expected 404, got {resp5.status_code}"

    print("PASS")
