"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds test data, and asserts the API returns the expected schema and values.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, VulnLink

    from .router import router

    # ------------------------------------------------------------------ #
    # In‑memory SQLite setup
    # ------------------------------------------------------------------ #
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed test rows
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    earlier = now - timedelta(days=1)

    today_str = now.date().isoformat()

    sess = TestingSession()
    sess.add(
        VulnLink(
            id=1,
            advisory_id="adv-1",
            server_id="srv-1",
            match_basis="basisA",
            match_value="valA",
            match_confidence=0.9,
            linked_at=now,
        )
    )
    sess.add(
        VulnLink(
            id=2,
            advisory_id="adv-2",
            server_id="srv-2",
            match_basis="basisB",
            match_value="valB",
            match_confidence=0.8,
            linked_at=now,
        )
    )
    sess.add(
        VulnLink(
            id=3,
            advisory_id="adv-3",
            server_id="srv-3",
            match_basis="basisA",
            match_value="valC",
            match_confidence=0.85,
            linked_at=earlier,
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override for the FastAPI app
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute test request
    # ------------------------------------------------------------------ #
    client = TestClient(app)

    days = 2
    resp = client.get(f"/api/vuln/link-activity?days={days}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # ------------------------------------------------------------------ #
    # Assertions per contract
    # ------------------------------------------------------------------ #
    assert body["days"] == days, body
    # series must contain today's date with count 2
    series_entry = next(
        (item for item in body["series"] if item["date"] == today_str), None
    )
    assert series_entry is not None, f"missing series entry for {today_str}"
    assert series_entry["count"] == 2, series_entry

    # two distinct match_basis values
    assert len(body["by_match_basis"]) == 2, body["by_match_basis"]
    # total links across the window
    assert body["total_links"] == 3, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)