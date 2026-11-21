"""contract.py -- the service's ACCEPTANCE self‑test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds test data, overrides the real DB session, and verifies the HTTP
response shape and content.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

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
    from app.models import Base, McpLlmAxisScore

    # Service router
    from .router import router

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite engine and create the schema
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------
    # Seed three rows:
    #   * two distinct model_version values (m1, m2)
    #   * one row with empty model_version → treated as 'unknown'
    # ------------------------------------------------------------------
    now = datetime.utcnow()
    session = TestingSession()
    session.add(
        McpLlmAxisScore(
            id=1,
            axis_name="axis1",
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="a1",
            server_id="srv1",
            scored_at=now - timedelta(days=2),
        )
    )
    session.add(
        McpLlmAxisScore(
            id=2,
            axis_name="axis1",
            decision_rule_version="v2",
            model_version="m2",
            adapter_sha256="a2",
            server_id="srv2",
            scored_at=now - timedelta(days=1),
        )
    )
    session.add(
        McpLlmAxisScore(
            id=3,
            axis_name="axis1",
            decision_rule_version=None,          # will be bucketed as 'unknown'
            model_version="",                    # empty string → 'unknown'
            adapter_sha256="a3",
            server_id="srv3",
            scored_at=now,
        )
    )
    session.commit()
    session.close()

    # ------------------------------------------------------------------
    # Dependency override that yields a fresh testing session per request
    # ------------------------------------------------------------------
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Build FastAPI app, mount router, inject override
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute request and validate response
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/scoring/model-versions")
    assert response.status_code == 200, response.text
    body = response.json()

    # totals
    totals = body.get("totals", {})
    assert totals.get("rows") == 3, totals
    # distinct_models should count the two real versions plus the 'unknown' bucket
    assert totals.get("distinct_models") == 3, totals

    # ordering: most recent `last_seen` first
    versions = body.get("versions", [])
    assert len(versions) == 3, body
    last_seen_vals = [v["last_seen"] for v in versions]
    # ISO‑8601 strings compare lexicographically in chronological order
    assert last_seen_vals == sorted(last_seen_vals, reverse=True), last_seen_vals

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)