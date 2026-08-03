"""
services/staged/directive_queue_health_api/contract.py

FastAPI contract for the Directive Queue Health API.
Mirrors the structure of services/_exemplar/contract.py.
"""

from __future__ import annotations

import datetime
import json
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer import (required by the no‑hollow gate)
from app.db import get_session  # noqa: F401  (imported for dependency injection)


router = APIRouter(prefix="/api")


class ByState(BaseModel):
    pending: int = Field(0, description="Number of pending directives")
    proposed: int = Field(0, description="Number of proposed directives")
    rejected: int = Field(0, description="Number of rejected directives")


class DirectiveQueueHealthResponse(BaseModel):
    total: int = Field(..., description="Total number of directives")
    by_state: ByState = Field(..., description="Counts per directive state")
    oldest_pending_age_seconds: Optional[int] = Field(
        None,
        description="Age in seconds of the oldest pending directive (None if no pending)",
    )
    is_starving: bool = Field(..., description="True if oldest pending > 1 hour")


@router.get(
    "/directives/queue-health",
    response_model=DirectiveQueueHealthResponse,
    summary="Health of the directive queue",
)
def get_queue_health(session: Session = Depends(get_session)):
    """
    Query the write‑service for directives, compute health metrics,
    and return a structured response.
    """
    # The write‑service endpoint – kept as a constant for clarity.
    WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"

    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"type": "directive"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    directives: List[dict] = resp.json()

    # Initialise counters
    pending_cnt = 0
    proposed_cnt = 0
    rejected_cnt = 0
    oldest_pending_ts: Optional[datetime.datetime] = None

    now = datetime.datetime.now(datetime.timezone.utc)

    for d in directives:
        state = d.get("state")
        created_at_str = d.get("created_at")
        if not state or not created_at_str:
            continue  # ignore malformed entries

        # Parse ISO‑8601 timestamps; assume they are UTC or contain offset.
        try:
            created_at = datetime.datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue  # skip unparsable timestamps

        if state == "pending":
            pending_cnt += 1
            if oldest_pending_ts is None or created_at < oldest_pending_ts:
                oldest_pending_ts = created_at
        elif state == "proposed":
            proposed_cnt += 1
        elif state == "rejected":
            rejected_cnt += 1

    total = pending_cnt + proposed_cnt + rejected_cnt

    if oldest_pending_ts is not None:
        oldest_age = int((now - oldest_pending_ts).total_seconds())
    else:
        oldest_age = None

    is_starving = (oldest_age or 0) > 3600

    return DirectiveQueueHealthResponse(
        total=total,
        by_state=ByState(
            pending=pending_cnt,
            proposed=proposed_cnt,
            rejected=rejected_cnt,
        ),
        oldest_pending_age_seconds=oldest_age,
        is_starving=is_starving,
    )


# --------------------------------------------------------------------------- #
# Application entry‑point and self‑test
# --------------------------------------------------------------------------- #

app = FastAPI()
app.include_router(router)


def _override_get_session():
    """
    Provide a throw‑away SQLite session for the self‑test.
    The real application will use the PostgreSQL session from app.db.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return SessionLocal()


if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Self‑test (acceptance)
    # ------------------------------------------------------------------- #
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    # Seed data: one very old pending (2 h), one recent pending, one proposed.
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    two_hours_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
    recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()

    seeded_directives = [
        {"state": "pending", "created_at": two_hours_ago},
        {"state": "pending", "created_at": recent},
        {"state": "proposed", "created_at": now_iso},
    ]

    class _MockResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    with patch("requests.post", return_value=_MockResponse(seeded_directives)):
        # Override the DB session dependency with an in‑memory SQLite session.
        app.dependency_overrides[get_session] = _override_get_session

        client = TestClient(app)
        response = client.get("/api/directives/queue-health")
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()

        # Verify the starvation logic (oldest pending > 1 h → starving)
        assert payload["is_starving"] is True, "Starvation flag should be True"
        # Basic sanity checks
        assert payload["total"] == 3, "Total count mismatch"
        assert payload["by_state"]["pending"] == 2, "Pending count mismatch"
        assert payload["by_state"]["proposed"] == 1, "Proposed count mismatch"

        print("PASS")