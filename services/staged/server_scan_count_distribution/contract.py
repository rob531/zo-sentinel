"""contract.py -- acceptance self‑test for the
server_scan_count_distribution service.

Running this module as a script executes a hermetic test that:

* builds an in‑memory SQLite DB with the real ORM model
* seeds it with a few rows of `McpServerRegistry`
* starts a FastAPI app with the service router
* overrides the app's DB dependency to use the test session
* issues a GET request to the endpoint
* validates the response shape and some key values

If everything is correct the script prints ``PASS`` and exits with status 0.
Any exception or failed assertion results in ``FAIL`` and a non‑zero exit
code.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Real application imports – these must stay unchanged.
from app.db import get_session
from app.models import Base, McpServerRegistry

# Router that implements the service endpoint.
from .router import router


def _override() -> Generator:
    """Yield a DB session bound to the in‑memory SQLite engine."""
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def run() -> bool:
    """Execute the self‑test.

    Returns:
        True if all assertions pass.
    """
    # ------------------------------------------------------------------ #
    # 1️⃣  Build an in‑memory SQLite engine and create the schema.
    # ------------------------------------------------------------------ #
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)

    # ------------------------------------------------------------------ #
    # 2️⃣  Prepare a session factory bound to the test engine.
    # ------------------------------------------------------------------ #
    global TestingSession  # used by the override defined above
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 3️⃣  Seed the database with four representative rows.
    # ------------------------------------------------------------------ #
    s = TestingSession()
    s.add(
        McpServerRegistry(
            server_id="srv0",
            name="Srv0",
            risk_tier="LOW",
            scan_count=0,
            last_scanned=None,
            registry_source="src1",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv1",
            name="Srv1",
            risk_tier="MEDIUM",
            scan_count=1,
            last_scanned=datetime(2023, 1, 1, 0, 0, 0),
            registry_source="src2",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv2",
            name="Srv2",
            risk_tier="HIGH",
            scan_count=5,
            last_scanned=datetime(2023, 2, 1, 0, 0, 0),
            registry_source="src1",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv3",
            name="Srv3",
            risk_tier="CRITICAL",
            scan_count=12,
            last_scanned=datetime(2023, 3, 1, 0, 0, 0),
            registry_source="src2",
        )
    )
    s.commit()
    s.close()

    # ------------------------------------------------------------------ #
    # 4️⃣  Build a FastAPI app, mount the router, and inject the test DB.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # 5️⃣  Exercise the endpoint via TestClient and assert expectations.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    resp = client.get("/api/registry/scan-count-buckets")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    # total number of servers
    assert body["total_servers"] == 4, body
    # bucket "0"
    bucket0 = next(b for b in body["buckets"] if b["bucket"] == "0")
    assert bucket0["server_count"] == 1, body
    assert bucket0["never_scanned"] == 1, body
    # at least two distinct registry_source entries
    assert len(body["by_source"]) >= 2, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – fail loudly
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)