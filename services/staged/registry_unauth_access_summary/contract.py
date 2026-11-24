"""contract.py -- the service's ACCEPTANCE self‑test.

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the service boots, mounts the router, returns 200 and a schema‑valid
body, the test prints ``PASS`` and exits 0.
"""
from __future__ import annotations

import sys
from typing import Generator

def run() -> bool:
    # FastAPI and testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Application DB session and ORM models
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    # Router under test
    from .router import router

    # ------------------------------------------------------------------ #
    # 1️⃣  Build an in‑memory SQLite DB that mirrors the real schema.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 2️⃣  Seed the DB with deterministic data.
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    # Server registry rows (three distinct servers, one with NULL risk_tier)
    sess.add(McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH"))
    sess.add(McpServerRegistry(server_id="srv2", name="Beta", risk_tier="MEDIUM"))
    sess.add(McpServerRegistry(server_id="srv3", name="Gamma", risk_tier=None))
    # Unauthenticated access rows (two unauth_access values, two sources)
    sess.add(
        McpUnauthAccess(
            server_id="srv1",
            unauth_access="none",
            source="sourceA",
            url="http://example.com/1",
        )
    )
    sess.add(
        McpUnauthAccess(
            server_id="srv2",
            unauth_access="basic",
            source="sourceA",
            url="http://example.com/2",
        )
    )
    sess.add(
        McpUnauthAccess(
            server_id="srv3",
            unauth_access="none",
            source="sourceB",
            url="http://example.com/3",
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Dependency override so the service uses the in‑memory session.
    # ------------------------------------------------------------------ #
    def _override() -> Generator[TestingSession, None, None]:
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # 4️⃣  Assemble the FastAPI app and mount the router.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # 5️⃣  Execute the request and validate the response.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/registry/unauth-access/summary")
    assert response.status_code == 200, response.text
    body = response.json()

    # totals.servers must equal the three distinct servers we seeded
    assert body["totals"]["servers"] == 3, body

    # by_unauth_access must contain the 'none' bucket with count 2
    none_bucket = next(
        (b for b in body["by_unauth_access"] if b["unauth_access"] == "none"), None
    )
    assert none_bucket is not None, body
    assert none_bucket["servers"] == 2, body

    # by_risk_tier must contain a NULL tier bucket with count 1
    null_tier_bucket = next(
        (b for b in body["by_risk_tier"] if b["risk_tier"] is None), None
    )
    assert null_tier_bucket is not None, body
    assert null_tier_bucket["servers"] == 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)