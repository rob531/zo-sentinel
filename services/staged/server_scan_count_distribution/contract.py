"""contract.py -- the service's ACCEPTANCE self‑test.

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds test data, hits the endpoint and validates the response shape and
some key values.

Exit code 0 prints ``PASS``; any exception prints ``FAIL`` and exits 1.
"""
from __future__ import annotations

import sys
from datetime import datetime

def run() -> bool:
    """Boot the FastAPI app with a test DB, hit the endpoint and assert."""
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy test engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real app data layer
    from app.db import get_session
    from app.models import Base, McpServerRegistry

    # Service router
    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite engine that mimics the real DB schema.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed the test data (4 rows, covering the required buckets).
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    sess.add(
        McpServerRegistry(
            server_id="srv0",
            name="S0",
            risk_tier="LOW",
            scan_count=0,
            last_scanned=None,
            registry_source="srcA",
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="S1",
            risk_tier="LOW",
            scan_count=1,
            last_scanned=datetime(2023, 1, 1, 0, 0, 0),
            registry_source="srcA",
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv2",
            name="S2",
            risk_tier="MEDIUM",
            scan_count=5,
            last_scanned=datetime(2023, 1, 2, 0, 0, 0),
            registry_source="srcB",
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv3",
            name="S3",
            risk_tier="HIGH",
            scan_count=12,
            last_scanned=datetime(2023, 1, 3, 0, 0, 0),
            registry_source="srcB",
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override that yields a fresh session per request.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Assemble the FastAPI app with the router and the overridden session.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute the request and validate the response.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/registry/scan-count-buckets")
    assert response.status_code == 200, response.text
    body = response.json()

    # Basic shape checks
    assert body["total_servers"] == 4, body
    # Bucket "0" must have one server and that server never scanned
    bucket_0 = next(b for b in body["buckets"] if b["bucket"] == "0")
    assert bucket_0["server_count"] == 1, body
    assert bucket_0["never_scanned"] == 1, body
    # At least two distinct registry_source entries
    assert len(body["by_source"]) >= 2, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)