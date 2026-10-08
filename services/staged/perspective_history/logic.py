# services/staged/perspective_history/logic.py

from datetime import datetime
from typing import List, Dict, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import PerspectiveSnapshot, PerspectiveEvent


def _build_summary(events: List[PerspectiveEvent]) -> Dict[str, Any]:
    total_servers = len({e.server_id for e in events})
    tier_distribution: Dict[str, int] = {}
    for ev in events:
        tier = ev.new_tier
        if tier:
            tier_distribution[tier] = tier_distribution.get(tier, 0) + 1
    return {"total_servers": total_servers, "tier_distribution": tier_distribution}


def get_perspective_history(
    perspective_id: int, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """Return history for a perspective.

    The response shape matches the contract used by downstream services.
    """
    snapshots = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .all()
    )
    events = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .all()
    )

    return {
        "perspective_id": perspective_id,
        "snapshots": [
            {"taken_at": s.taken_at.isoformat(), "membership": s.membership}
            for s in snapshots
        ],
        "events": [
            {
                "server_id": e.server_id,
                "change_type": e.change_type,
                "old_tier": e.old_tier,
                "new_tier": e.new_tier,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "summary": _build_summary(events),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB that mimics the real models
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Populate test data: 1 perspective, 2 snapshots, 3 events
    # ------------------------------------------------------------------- #
    db = SessionLocal()
    try:
        # Snapshots
        snap1 = PerspectiveSnapshot(
            perspective_id=1,
            taken_at=datetime(2023, 1, 10, 12, 0, 0),
            membership={"srv-1": "high", "srv-2": "low"},
        )
        snap2 = PerspectiveSnapshot(
            perspective_id=1,
            taken_at=datetime(2023, 1, 5, 12, 0, 0),
            membership={"srv-1": "medium"},
        )
        db.add_all([snap1, snap2])

        # Events
        ev1 = PerspectiveEvent(
            perspective_id=1,
            server_id="srv-1",
            change_type="tier_change",
            old_tier="low",
            new_tier="high",
            seen=False,
            created_at=datetime(2023, 1, 11, 9, 0, 0),
        )
        ev2 = PerspectiveEvent(
            perspective_id=1,
            server_id="srv-2",
            change_type="tier_change",
            old_tier="none",
            new_tier="low",
            seen=False,
            created_at=datetime(2023, 1, 9, 15, 30, 0),
        )
        ev3 = PerspectiveEvent(
            perspective_id=1,
            server_id="srv-3",
            change_type="added",
            old_tier=None,
            new_tier="medium",
            seen=False,
            created_at=datetime(2023, 1, 8, 8, 45, 0),
        )
        db.add_all([ev1, ev2, ev3])

        db.commit()
    finally:
        db.close()

    # ------------------------------------------------------------------- #
    # Build a tiny FastAPI app that uses the logic
    # ------------------------------------------------------------------- #
    app = FastAPI()

    @app.get("/api/perspectives/{perspective_id}/history")
    async def history_endpoint(
        perspective_id: int,
        data: Dict[str, Any] = Depends(get_perspective_history),
    ):
        return data

    # Override the session dependency with our in‑memory session factory
    def _override_get_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the contract test
    # ------------------------------------------------------------------- #
    response = client.get("/api/perspectives/1/history")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert len(payload["snapshots"]) == 2, "Expected 2 snapshots"
    assert "tier_distribution" in payload["summary"], "Missing tier_distribution"
    print("PASS")