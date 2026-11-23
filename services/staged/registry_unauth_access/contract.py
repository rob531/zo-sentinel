"""contract.py -- acceptance self‑test for the `registry_unauth_access` service.

Runs the service in an isolated FastAPI instance with an in‑memory SQLite
database, seeds minimal data, and asserts that the endpoint returns the
expected JSON shape and values.

Usage:
    python -m services.staged.registry_unauth_access.contract
"""
from __future__ import annotations

import sys

def run() -> bool:
    """Boot the service with a test DB, hit the endpoint and validate output."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer imports – required for a non‑hollow build
    from app.db import get_session
    from app.models import Base, McpUnauthAccess, McpServerRegistry

    # Router containing the actual endpoint implementation
    from .router import router

    # ------------------------------------------------------------------ #
    # 1️⃣  In‑memory SQLite engine (StaticPool) and schema creation
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------ #
    # 2️⃣  Seed the test data
    # ------------------------------------------------------------------ #
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    sess = TestingSession()
    # Registry row (only one, others will appear with null name/tier)
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="Server One",
            risk_tier="HIGH",
        )
    )
    # Unauthenticated‑access rows across two sources
    sess.add_all(
        [
            McpUnauthAccess(
                server_id="srv1",
                source="src1",
                unauth_access="yes",
                url="http://example.com/1",
            ),
            McpUnauthAccess(
                server_id="srv2",
                source="src1",
                unauth_access="no",
                url="http://example.com/2",
            ),
            McpUnauthAccess(
                server_id="srv3",
                source="src2",
                unauth_access="maybe",
                url="http://example.com/3",
            ),
        ]
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Dependency override so the service uses the in‑memory DB
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # 4️⃣  Assemble FastAPI app with the router
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # 5️⃣  Execute test client calls and assert expectations
    # ------------------------------------------------------------------ #
    client = TestClient(app)

    # ---- default request (no source filter) ---------------------------------
    resp = client.get("/api/registry/unauth-access")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3, body
    assert isinstance(body["sources"], list), body
    assert len(body["sources"]) == 2, body
    # Build a dict source -> count for easy checking
    src_counts = {src["source"]: src["count"] for src in body["sources"]}
    assert src_counts.get("src1") == 2, body
    assert src_counts.get("src2") == 1, body

    # ---- source‑filtered request ---------------------------------------------
    resp_src = client.get("/api/registry/unauth-access", params={"source": "src1"})
    assert resp_src.status_code == 200, resp_src.text
    body_src = resp_src.json()
    assert body_src["total"] == 2, body_src
    assert isinstance(body_src["sources"], list), body_src
    assert len(body_src["sources"]) == 1, body_src
    assert body_src["sources"][0]["source"] == "src1", body_src
    assert body_src["sources"][0]["count"] == 2, body_src

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – fail loudly
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)