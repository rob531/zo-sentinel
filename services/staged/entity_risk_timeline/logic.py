# services/staged/entity_risk_timeline/logic.py
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real application data layer
from app.db import Base, get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api")


class TierTransition(BaseModel):
    date: datetime = Field(..., description="Timestamp of the change")
    old_tier: Optional[int] = Field(None, description="Previous risk tier")
    new_tier: Optional[int] = Field(None, description="New risk tier")
    change_type: Optional[str] = Field(None, description="Type of change")


class RiskTimelineResponse(BaseModel):
    server_id: str
    days: int
    total_changes: int
    tier_transitions: List[TierTransition]


@router.get(
    "/entities/{server_id}/risk-timeline",
    response_model=RiskTimelineResponse,
    name="entity_risk_timeline",
)
def get_risk_timeline(
    server_id: str,
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
):
    """
    Return the risk‑tier transition history for a given server over the past *days*.
    """
    # Verify the server exists
    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    start_dt = datetime.utcnow() - timedelta(days=days)

    events = (
        session.query(PerspectiveEvent)
        .filter(
            PerspectiveEvent.server_id == server_id,
            PerspectiveEvent.created_at >= start_dt,
        )
        .order_by(PerspectiveEvent.created_at)
        .all()
    )

    transitions = [
        TierTransition(
            date=ev.created_at,
            old_tier=ev.old_tier,
            new_tier=ev.new_tier,
            change_type=ev.change_type,
        )
        for ev in events
    ]

    return RiskTimelineResponse(
        server_id=server_id,
        days=days,
        total_changes=len(transitions),
        tier_transitions=transitions,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real models
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    # Dependency override for the test client
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed data: three servers with tier transitions over three days
    # ------------------------------------------------------------------- #
    test_session = TestingSessionLocal()
    try:
        # Servers
        servers = [
            McpServerRegistry(server_id="srv1", name="Server One", risk_tier=1),
            McpServerRegistry(server_id="srv2", name="Server Two", risk_tier=2),
            McpServerRegistry(server_id="srv3", name="Server Three", risk_tier=3),
        ]
        test_session.add_all(servers)
        test_session.flush()  # obtain PKs if needed

        now = datetime.utcnow()
        events = [
            # srv1: two transitions
            PerspectiveEvent(
                server_id="srv1",
                perspective_id=1,
                created_at=now - timedelta(days=2),
                old_tier=0,
                new_tier=1,
                change_type="initial",
                seen=True,
            ),
            PerspectiveEvent(
                server_id="srv1",
                perspective_id=1,
                created_at=now - timedelta(days=1),
                old_tier=1,
                new_tier=2,
                change_type="upgrade",
                seen=True,
            ),
            # srv2: one transition
            PerspectiveEvent(
                server_id="srv2",
                perspective_id=1,
                created_at=now - timedelta(days=1),
                old_tier=2,
                new_tier=3,
                change_type="upgrade",
                seen=True,
            ),
            # srv3: no transitions (to test empty list)
        ]
        test_session.add_all(events)
        test_session.commit()
    finally:
        test_session.close()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with router and override the session dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request and validate response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/entities/srv1/risk-timeline?days=3")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    expected_changes = 2
    if data.get("total_changes") != expected_changes:
        print(
            f"FAIL: expected {expected_changes} changes, got {data.get('total_changes')}",
            file=sys.stderr,
        )
        sys.exit(1)

    first = data["tier_transitions"][0]
    if first["old_tier"] != 0:
        print(
            f"FAIL: first transition old_tier expected 0, got {first['old_tier']}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")