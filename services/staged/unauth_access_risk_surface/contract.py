"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB dependency, seeds required tables, issues a request
to the endpoint and validates the response.

Exit code 0 prints ``PASS``; any exception prints ``FAIL`` and exits 1.
"""
from __future__ import annotations

import sys
from datetime import datetime

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy core for an in‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real application data layer
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    # Service router
    from .router import router

    # ------------------------------------------------------------------ #
    # 1️⃣  Build an in‑memory SQLite engine and create the schema tables.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------ #
    # 2️⃣  Prepare a session factory bound to the in‑memory engine.
    # ------------------------------------------------------------------ #
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 3️⃣  Seed the required tables.
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    # Registry rows (two matching, one missing)
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="Alpha",
            risk_tier="HIGH",
            trust_score=0.92,
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv2",
            name="Beta",
            risk_tier="LOW",
            trust_score=0.45,
        )
    )
    # No registry entry for srv3 – it will appear with risk_tier = null.

    # Unauthenticated‑access rows (three total)
    now = datetime.utcnow()
    sess.add(
        McpUnauthAccess(
            server_id="srv1",
            url="https://alpha.example.com",
            unauth_access="full",
            source="test",
            updated_at=now,
        )
    )
    sess.add(
        McpUnauthAccess(
            server_id="srv2",
            url="https://beta.example.com",
            unauth_access="partial",
            source="test",
            updated_at=now,
        )
    )
    sess.add(
        McpUnauthAccess(
            server_id="srv3",
            url="https://gamma.example.com",
            unauth_access="none",
            source="test",
            updated_at=now,
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # 4️⃣  Dependency override – each request gets a fresh session bound to
    #     the in‑memory engine.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # 5️⃣  Build the FastAPI app, mount the router and apply the override.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # 6️⃣  Exercise the endpoint via TestClient and assert the contract.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/risk/unauth-access")
    assert response.status_code == 200, response.text
    body = response.json()

    # Total rows must equal the three seeded unauth rows.
    assert body["total"] == 3, body

    # Access‑mode aggregation.
    assert body["by_access_mode"]["full"] == 1, body
    assert body["by_access_mode"]["partial"] == 1, body
    assert body["by_access_mode"]["none"] == 1, body

    # The registry‑less entry must have risk_tier == null (None in Python).
    srv3_entry = next(s for s in body["servers"] if s["server_id"] == "srv3")
    assert srv3_entry["risk_tier"] is None, body

    # Servers list length must be three.
    assert len(body["servers"]) == 3, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)