"""
services/staged/perspective_event_api/contract.py

FastAPI contract for the `perspective_event_api` service.

Provides:
- GET /api/perspectives/{perspective_id}/events
- GET /api/perspectives/{perspective_id}/events/{event_id}

The module uses the real application data layer (`app.db.get_session` and
`app.models.PerspectiveEvent`).  The `__main__` block runs a self‑test that
overrides the session dependency with an in‑memory SQLite database, seeds
sample data, and validates the responses.
"""

from __future__ import annotations

from typing import List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real application imports – these must remain unchanged.
from app.db import get_session
from app.models import PerspectiveEvent, Base  # `Base` is the declarative base.

app = FastAPI()


# --------------------------------------------------------------------------- #
# Pydantic response model
# --------------------------------------------------------------------------- #
class PerspectiveEventResponse(BaseModel):
    server_id: int
    change_type: str
    old_tier: str
    new_tier: str
    seen: bool

    class Config:
        orm_mode = True


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get(
    "/api/perspectives/{perspective_id}/events",
    response_model=List[PerspectiveEventResponse],
)
def list_perspective_events(
    perspective_id: int,
    db: Session = Depends(get_session),
):
    """Return all events for a given perspective."""
    events = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.id)
        .all()
    )
    return events


@app.get(
    "/api/perspectives/{perspective_id}/events/{event_id}",
    response_model=PerspectiveEventResponse,
)
def get_perspective_event(
    perspective_id: int,
    event_id: int,
    db: Session = Depends(get_session),
):
    """Return a single event for a given perspective."""
    event = (
        db.query(PerspectiveEvent)
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.id == event_id,
        )
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.perspective_event_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite engine and a fresh session factory.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create all tables defined in the application's declarative base.
    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Seed test data: 3 perspectives, each with 5 events.
    # ------------------------------------------------------------------- #
    def seed_data() -> None:
        with SessionLocal() as db:
            for perspective_id in range(1, 4):
                for i in range(5):
                    ev = PerspectiveEvent(
                        perspective_id=perspective_id,
                        server_id=1000 + perspective_id * 10 + i,
                        change_type="upgrade" if i % 2 == 0 else "downgrade",
                        old_tier="low",
                        new_tier="high",
                        seen=(i % 3 == 0),
                    )
                    db.add(ev)
            db.commit()

    seed_data()

    # ------------------------------------------------------------------- #
    # Dependency override: replace the real DB session with our test session.
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Validate list endpoint for each perspective.
    # ------------------------------------------------------------------- #
    for pid in range(1, 4):
        resp = client.get(f"/api/perspectives/{pid}/events")
        assert resp.status_code == 200, f"List endpoint failed for perspective {pid}"
        data = resp.json()
        assert isinstance(data, list), "Response is not a list"
        assert len(data) == 5, f"Expected 5 events for perspective {pid}, got {len(data)}"
        for ev in data:
            for field in ("server_id", "change_type", "old_tier", "new_tier", "seen"):
                assert field in ev, f"Missing field '{field}' in event"

        # ---------------------------------------------------------------- #
        # Validate single‑event endpoint (first event of the list).
        # ---------------------------------------------------------------- #
        first_event_id = data[0]["id"] if "id" in data[0] else None
        if first_event_id is not None:
            single_resp = client.get(
                f"/api/perspectives/{pid}/events/{first_event_id}"
            )
            assert (
                single_resp.status_code == 200
            ), f"Single event endpoint failed for perspective {pid}"
            single_data = single_resp.json()
            for field in ("server_id", "change_type", "old_tier", "new_tier", "seen"):
                assert field in single_data, f"Missing field '{field}' in single event"

    print("PASS")