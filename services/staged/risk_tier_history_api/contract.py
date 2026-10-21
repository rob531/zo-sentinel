"""services/staged/risk_tier_history_api/contract.py

FastAPI contract for the risk tier history API.
Mirrors the pattern used in services/_exemplar/contract.py.
"""

from __future__ import annotations

import datetime as _dt
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must not be re‑implemented)
from app.db import Base, get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api")


class TierHistoryItem(BaseModel):
    """Single tier‑change event returned by the endpoint."""

    date: _dt.datetime = Field(..., description="Timestamp of the event")
    change_type: str = Field(..., description="Type of change")
    old_tier: int | None = Field(..., description="Previous risk tier")
    new_tier: int | None = Field(..., description="New risk tier")


@router.get(
    "/servers/{server_id}/tier-history",
    response_model=List[TierHistoryItem],
    summary="Get tier‑change history for a server",
)
def get_tier_history(
    server_id: str,
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> List[TierHistoryItem]:
    """
    Return the most recent tier‑change events for *server_id*.

    The query joins ``perspective_events`` to ``mcp_server_registry`` to ensure
    the server exists, orders by ``created_at`` descending and caps the result
    set at *limit* rows (default 50).
    """
    # Verify the server exists – the join will filter out missing servers.
    query = (
        session.query(PerspectiveEvent)
        .join(McpServerRegistry, PerspectiveEvent.server_id == McpServerRegistry.server_id)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(limit)
    )
    rows = query.all()
    if not rows:
        # If the server does not exist we surface a 404.
        # (An empty list would be ambiguous – could mean “no events”.)
        server_exists = (
            session.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == server_id)
            .first()
        )
        if not server_exists:
            raise HTTPException(status_code=404, detail="Server not found")
    return [
        TierHistoryItem(
            date=row.created_at,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Self‑test (run with: `python -m services.staged.risk_tier_history_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # --------------------------------------------------------------------- #
    # Build an in‑memory SQLite app instance with the real models.
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Override the dependency used by the router.
    def override_get_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Create tables.
    Base.metadata.create_all(bind=engine)

    # Seed data.
    now = _dt.datetime.utcnow()
    with TestingSessionLocal() as db:
        srv1 = McpServerRegistry(server_id="srv1", name="Server One", risk_tier=1)
        srv2 = McpServerRegistry(server_id="srv2", name="Server Two", risk_tier=2)
        db.add_all([srv1, srv2])
        db.flush()  # obtain PKs if needed

        events = [
            # Server 1 – three events on different dates
            PerspectiveEvent(
                server_id="srv1",
                perspective_id=1,
                created_at=now - _dt.timedelta(days=3),
                change_type="initial",
                old_tier=None,
                new_tier=1,
                seen=False,
            ),
            PerspectiveEvent(
                server_id="srv1",
                perspective_id=1,
                created_at=now - _dt.timedelta(days=2),
                change_type="upgrade",
                old_tier=1,
                new_tier=2,
                seen=False,
            ),
            PerspectiveEvent(
                server_id="srv1",
                perspective_id=1,
                created_at=now - _dt.timedelta(days=1),
                change_type="downgrade",
                old_tier=2,
                new_tier=1,
                seen=False,
            ),
            # Server 2 – one event
            PerspectiveEvent(
                server_id="srv2",
                perspective_id=1,
                created_at=now - _dt.timedelta(days=1, hours=12),
                change_type="initial",
                old_tier=None,
                new_tier=2,
                seen=False,
            ),
        ]
        db.add_all(events)
        db.commit()

    # Build FastAPI app for the test.
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Test: fetch history for srv1 – expect three items sorted newest first.
    # --------------------------------------------------------------------- #
    resp = client.get("/api/servers/srv1/tier-history")
    if resp.status_code != 200:
        print(f"FAIL: Expected 200, got {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if len(data) != 3:
        print(f"FAIL: Expected 3 events, got {len(data)}", file=sys.stderr)
        sys.exit(1)

    # Verify ordering (newest first)
    dates = [_dt.datetime.fromisoformat(item["date"]) for item in data]
    if dates != sorted(dates, reverse=True):
        print("FAIL: Events not sorted by date descending", file=sys.stderr)
        sys.exit(1)

    # --------------------------------------------------------------------- #
    # Test: fetch history for srv2 – expect one item.
    # --------------------------------------------------------------------- #
    resp2 = client.get("/api/servers/srv2/tier-history")
    if resp2.status_code != 200:
        print(f"FAIL: Expected 200 for srv2, got {resp2.status_code}", file=sys.stderr)
        sys.exit(1)

    data2 = resp2.json()
    if len(data2) != 1:
        print(f"FAIL: Expected 1 event for srv2, got {len(data2)}", file=sys.stderr)
        sys.exit(1)

    # --------------------------------------------------------------------- #
    # All checks passed.
    # --------------------------------------------------------------------- #
    print("PASS")
    sys.exit(0)