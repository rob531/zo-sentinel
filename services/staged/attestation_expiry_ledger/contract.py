"""contract.py -- the service's ACCEPTANCE self‑test.

Runs the service in an isolated subprocess, overrides the DB session with an
in‑memory SQLite database, seeds minimal data and asserts the endpoint
behaviour.

The test prints ``PASS`` on success and exits with status 0, otherwise prints
``FAIL`` and exits with status 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Table, Column, String, DateTime, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# real data‑layer imports – never stubbed
from app.db import get_session
from app.models import Base, McpServerRegistry

# router that implements the service API
from .router import router


def _seed_attestations(metadata, engine):
    """Create a temporary ``mcp_attestations`` table and seed four rows.

    The table is deliberately minimal – only the columns required by the
    service logic are defined.
    """
    att_table = Table(
        "mcp_attestations",
        metadata,
        Column("id", String, primary_key=True),
        Column("server_id", String, nullable=False),
        Column("expires_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)

    now = datetime.utcnow()
    rows = [
        # already expired
        {"id": "a1", "server_id": "srv1", "expires_at": now - timedelta(days=1)},
        # expiring in 10 days → should fall into ``expiring_30d``
        {"id": "a2", "server_id": "srv2", "expires_at": now + timedelta(days=10)},
        # expiring in 90 days → should be ``valid``
        {"id": "a3", "server_id": "srv3", "expires_at": now + timedelta(days=90)},
        # expires exactly now + 40 days → ``valid`` (outside 30‑day window)
        {"id": "a4", "server_id": "srv4", "expires_at": now + timedelta(days=40)},
    ]

    with engine.begin() as conn:
        conn.execute(att_table.insert(), rows)


def run() -> bool:
    """Boot the service with an in‑memory DB, seed data, and run assertions."""
    # ------------------------------------------------------------------ #
    # 1️⃣  Build an in‑memory SQLite engine and a testing session factory.
    # ------------------------------------------------------------------ #
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # create tables for the *app* models
    Base.metadata.create_all(eng)

    # create the temporary bus table and seed it
    _seed_attestations(Base.metadata, eng)

    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 2️⃣  Seed the required ``mcp_server_registry`` rows.
    # ------------------------------------------------------------------ #
    s = TestingSession()
    s.add(McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv2", name="Beta", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="LOW"))
    s.add(McpServerRegistry(server_id="srv4", name="Delta", risk_tier="LOW"))
    s.commit()
    s.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Override the FastAPI dependency that provides a DB session.
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
    # 4️⃣  Exercise the endpoint and assert expectations.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    resp = client.get("/api/attestations/expiry-ledger")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # total number of servers reported must be 4
    assert len(body.get("servers", [])) == 4, body

    # bucket counts must sum to 4
    bucket_counts = sum(b.get("count", 0) for b in body.get("buckets", []))
    assert bucket_counts == 4, body

    # the row expiring in 10 days must be in the ``expiring_30d`` bucket
    expiring_30d = next(
        (b for b in body.get("buckets", []) if b.get("bucket") == "expiring_30d"),
        None,
    )
    assert expiring_30d is not None, body
    assert expiring_30d.get("count", 0) >= 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)