# deps: fastapi, pydantic, sqlalchemy
"""services.active.verdict_watchlist_api.router

GET /api/verdict/watchlist  -- recent risk tier transition events (PerspectiveEvent)
                             joined to McpServerRegistry for server name/tier.

No auth, no org_id scoping (PerspectiveEvent is global; the directive marks
auth=public). No DB writes.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from importlib.util import spec_from_file_location, module_from_spec
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

# Resolve the repo root once so we can load app/db.py and app/models.py
# directly, bypassing app/__init__.py (which has broken router imports).
_REPO_ROOT = os.environ.get("ZO_SENTINEL_ROOT", "/home/workspace/zo_sentinel")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Load app.db directly (bypass app/__init__.py)
_db_spec = spec_from_file_location("app.db", os.path.join(_REPO_ROOT, "app", "db.py"))
_db_mod = module_from_spec(_db_spec)
_db_spec.loader.exec_module(_db_mod)  # type: ignore
get_session = _db_mod.get_session
Base = _db_mod.Base

# Load app.models directly (bypass app/__init__.py)
_models_spec = spec_from_file_location("app.models", os.path.join(_REPO_ROOT, "app", "models.py"))
_models_mod = module_from_spec(_models_spec)
_models_spec.loader.exec_module(_models_mod)  # type: ignore
McpServerRegistry = _models_mod.McpServerRegistry
PerspectiveEvent = _models_mod.PerspectiveEvent

router = APIRouter(prefix="/api", tags=["verdict_watchlist_api"])


# ---- Pydantic response shapes ----

class WatchlistEvent(BaseModel):
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    change_type: Optional[str]
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: str   # ISO-8601

    class Config:
        from_attributes = True


class WatchlistResponse(BaseModel):
    total: int
    events: List[WatchlistEvent]


# ---- Endpoint ----

@router.get("/verdict/watchlist", response_model=WatchlistResponse)
def get_verdict_watchlist(
    limit: int = Query(50, ge=1, le=500, description="Max events to return"),
    perspective_id: Optional[int] = Query(None, description="Filter by perspective"),
    unseen_only: bool = Query(False, description="Return only unseen events"),
    db: Session = Depends(get_session),
) -> WatchlistResponse:
    """
    Return recent PerspectiveEvent rows joined to McpServerRegistry.

    Serves as the watchlist: a flat, recent-first feed of risk-tier
    transitions for all servers across all perspectives.

    - `perspective_id` filters to a specific perspective.
    - `unseen_only=True` returns only events with seen=False.
    - `limit` caps the result (default 50, max 500).
    """
    stmt = (
        select(PerspectiveEvent, McpServerRegistry)
        .join(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.server_id,
            isouter=True,
        )
    )

    if perspective_id is not None:
        stmt = stmt.where(PerspectiveEvent.perspective_id == perspective_id)

    if unseen_only:
        stmt = stmt.where(PerspectiveEvent.seen == False)  # noqa: E712

    stmt = stmt.order_by(PerspectiveEvent.created_at.desc()).limit(limit)

    rows = db.execute(stmt).all()

    events = [
        WatchlistEvent(
            server_id=pe.server_id,
            server_name=sr.name if sr else None,
            risk_tier=sr.risk_tier if sr else None,
            change_type=pe.change_type,
            old_tier=pe.old_tier,
            new_tier=pe.new_tier,
            seen=pe.seen,
            created_at=pe.created_at.isoformat() if pe.created_at else "",
        )
        for pe, sr in rows
    ]

    return WatchlistResponse(total=len(events), events=events)


# --------------------------------------------------------------------------- #
# Self-test (run: python services/active/verdict_watchlist_api/router.py)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    # seed
    with SessionLocal() as s:
        s.add(McpServerRegistry(server_id="srv-1", name="Alpha", risk_tier="high", url="https://example.com"))
        s.add(McpServerRegistry(server_id="srv-2", name="Beta",  risk_tier="low",  url="https://github.com/test"))
        s.add(PerspectiveEvent(perspective_id=1, server_id="srv-1", change_type="tier_change",
                              old_tier="low",    new_tier="high", seen=False,
                              created_at=datetime(2026, 1, 6, tzinfo=timezone.utc)))
        s.add(PerspectiveEvent(perspective_id=1, server_id="srv-2", change_type="tier_change",
                              old_tier="medium", new_tier="low",  seen=True,
                              created_at=datetime(2026, 1, 5, tzinfo=timezone.utc)))
        s.add(PerspectiveEvent(perspective_id=2, server_id="srv-1", change_type="tier_change",
                              old_tier="high",   new_tier="critical", seen=False,
                              created_at=datetime(2026, 1, 7, tzinfo=timezone.utc)))
        s.commit()

    def _override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Happy path: returns events, ordered desc
    resp = client.get("/api/verdict/watchlist")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "events" in body
    assert "total" in body
    assert body["total"] == 3, f"Expected 3 events, got {body['total']}"
    assert len(body["events"]) == 3
    # Most recent first
    assert body["events"][0]["server_id"] == "srv-1"
    assert body["events"][0]["new_tier"] == "critical"

    # unseen_only filter
    resp2 = client.get("/api/verdict/watchlist?unseen_only=true")
    assert resp2.status_code == 200
    unseen = resp2.json()["events"]
    assert all(not e["seen"] for e in unseen), "All returned should be unseen"

    # perspective_id filter
    resp3 = client.get("/api/verdict/watchlist?perspective_id=1")
    assert resp3.status_code == 200
    p1_events = resp3.json()["events"]
    assert all(e["server_id"] in ("srv-1", "srv-2") for e in p1_events)

    # limit
    resp4 = client.get("/api/verdict/watchlist?limit=1")
    assert resp4.status_code == 200
    assert len(resp4.json()["events"]) == 1

    # Empty result for non-existent perspective
    resp5 = client.get("/api/verdict/watchlist?perspective_id=9999")
    assert resp5.status_code == 200
    assert resp5.json()["total"] == 0

    print("PASS")
