"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the service boots, mounts, serves 200 and returns a schema‑valid body,
the script prints ``PASS`` and exits 0. Any exception causes ``FAIL`` and
a non‑zero exit.
"""
from __future__ import annotations

import sys


def run() -> bool:
    """Boot the service with a temporary SQLite DB and exercise its endpoint."""
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data‑layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    # Service router
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
    # 2️⃣  Seed the DB:
    #   • srv1 – registered, tier HIGH, has unauth rows.
    #   • srv2 – registered, tier LOW,  no unauth rows.
    #   • srv3 – **no** registry entry, has unauth rows (unmapped).
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    sess.add(McpServerRegistry(server_id="srv1", name="Server1", risk_tier="HIGH"))
    sess.add(McpServerRegistry(server_id="srv2", name="Server2", risk_tier="LOW"))
    # Unauthenticated‑access findings
    sess.add(
        McpUnauthAccess(
            server_id="srv1",
            source="src",
            unauth_access=True,
            url="http://example.com/a",
        )
    )
    sess.add(
        McpUnauthAccess(
            server_id="srv3",
            source="src",
            unauth_access=True,
            url="http://example.com/b",
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Dependency override so the service uses the temporary session.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # 4️⃣  Assemble FastAPI app with the router and run the test client.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/unauth/tier-correlation")
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------ #
    # 5️⃣  Assertions matching the seeded data.
    # ------------------------------------------------------------------ #
    assert isinstance(body, dict), body
    assert len(body.get("tiers", [])) == 2, body
    assert body.get("unmapped_servers") == 1, body
    assert body.get("total_unauth_rows") == 2, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)