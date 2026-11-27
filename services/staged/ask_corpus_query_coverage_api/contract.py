"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the service boots, mounts, serves 200 and returns a schema‑valid body,
the test prints ``PASS`` and exits 0. Any exception causes ``FAIL`` and a
non‑zero exit code.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    """Boot the service with a temporary SQLite DB and run a basic acceptance test."""
    # FastAPI and testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy utilities for an in‑memory SQLite DB
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data‑layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, AskCorpusDoc, McpServerRegistry

    # Service router
    from .router import router

    # --------------------------------------------------------------------- #
    # Set up an in‑memory SQLite engine and create the required tables.
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # --------------------------------------------------------------------- #
    # Seed the database with deterministic test data.
    # --------------------------------------------------------------------- #
    now = datetime.utcnow()
    session = TestingSession()
    # Registry servers (4 total)
    session.add(McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH", verdict="PASS"))
    session.add(McpServerRegistry(server_id="srv2", name="Beta", risk_tier="MEDIUM", verdict="PASS"))
    session.add(McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="LOW", verdict="PASS"))
    session.add(McpServerRegistry(server_id="srv4", name="Delta", risk_tier="LOW", verdict="PASS"))
    # Corpus index entries (3 of the 4 servers)
    session.add(
        AskCorpusDoc(
            server_id="srv1",
            content_hash="hash1",
            indexed_at=now - timedelta(days=2),
            snippet="snippet1",
            terms="term1",
        )
    )
    session.add(
        AskCorpusDoc(
            server_id="srv2",
            content_hash="hash2",
            indexed_at=now - timedelta(days=5),
            snippet="snippet2",
            terms="term2",
        )
    )
    session.add(
        AskCorpusDoc(
            server_id="srv3",
            content_hash="hash3",
            indexed_at=now - timedelta(days=1),
            snippet="snippet3",
            terms="term3",
        )
    )
    session.commit()
    session.close()

    # --------------------------------------------------------------------- #
    # Dependency override so the service uses the in‑memory session.
    # --------------------------------------------------------------------- #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Build the FastAPI app, mount the router, and apply the override.
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # --------------------------------------------------------------------- #
    # Execute the request against the test client.
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/ask/coverage")  # default sample=20
    assert response.status_code == 200, response.text
    body = response.json()

    # --------------------------------------------------------------------- #
    # Validate the response payload.
    # --------------------------------------------------------------------- #
    assert body["total_indexed"] == 3, body
    assert body["total_registry"] == 4, body
    # coverage_ratio should be 3/4 = 0.75 (allow small floating‑point drift)
    assert abs(body["coverage_ratio"] - 0.75) < 0.01, body
    # Exactly one server is missing from the corpus index
    assert isinstance(body.get("missing_sample"), list), body
    assert len(body["missing_sample"]) == 1, body
    missing = body["missing_sample"][0]
    assert missing["server_id"] == "srv4", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)