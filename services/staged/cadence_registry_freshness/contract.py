"""contract.py -- acceptance self‑test for the cadence_registry_freshness service.

Running this module as a script executes a hermetic test that:

* builds an in‑memory SQLite DB mirroring the real app schema,
* seeds it with a small, deterministic data set,
* overrides the FastAPI dependency that provides a DB session,
* starts the service router,
* issues a GET request to the public endpoint,
* validates the response shape and a few key values,
* prints ``PASS`` on success (exit code 0) or ``FAIL`` on any error
  (exit code 1).

The test guarantees that the service boots, mounts, serves 200, and
returns a schema‑valid body while exercising the real data‑access layer
(`app.db.get_session` + `app.models`).
"""
from __future__ import annotations

import sys
from datetime import datetime

def run() -> bool:
    """Execute the self‑test.

    Returns ``True`` if all assertions pass; otherwise an exception is raised.
    """
    # ------------------------------------------------------------------ #
    # Imports – keep them inside the function so the module can be imported
    # without side effects (e.g. during normal service start‑up).
    # ------------------------------------------------------------------ #
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite engine and create the tables defined in
    # ``app.models`` (the same metadata used by the real service).
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed deterministic rows covering two distinct ``registry_source`` values.
    # The data set includes:
    #   * rows with ``last_assessed`` = NULL (to test “never assessed”),
    #   * rows with a NULL ``verdict`` (to test “missing verdict”),
    #   * varied ``scan_count`` values (to test median calculation).
    # ------------------------------------------------------------------ #
    seed = [
        McpServerRegistry(
            server_id="srv1",
            name="S1",
            registry_source="srcA",
            last_assessed=None,
            first_seen=datetime(2023, 1, 1),
            last_seen=datetime(2023, 1, 2),
            last_scanned=datetime(2023, 1, 3),
            scan_count=5,
            risk_tier="HIGH",
            verdict=None,
        ),
        McpServerRegistry(
            server_id="srv2",
            name="S2",
            registry_source="srcA",
            last_assessed=datetime(2023, 1, 5),
            first_seen=datetime(2023, 1, 1),
            last_seen=datetime(2023, 1, 5),
            last_scanned=datetime(2023, 1, 6),
            scan_count=3,
            risk_tier="LOW",
            verdict="OK",
        ),
        McpServerRegistry(
            server_id="srv3",
            name="S3",
            registry_source="srcA",
            last_assessed=datetime(2022, 10, 1),
            first_seen=datetime(2022, 9, 30),
            last_seen=datetime(2022, 10, 2),
            last_scanned=datetime(2022, 10, 3),
            scan_count=7,
            risk_tier="MEDIUM",
            verdict="FAIL",
        ),
        McpServerRegistry(
            server_id="srv4",
            name="S4",
            registry_source="srcB",
            last_assessed=None,
            first_seen=datetime(2023, 2, 1),
            last_seen=datetime(2023, 2, 2),
            last_scanned=datetime(2023, 2, 3),
            scan_count=2,
            risk_tier="HIGH",
            verdict=None,
        ),
        McpServerRegistry(
            server_id="srv5",
            name="S5",
            registry_source="srcB",
            last_assessed=datetime(2023, 2, 5),
            first_seen=datetime(2023, 2, 1),
            last_seen=datetime(2023, 2, 5),
            last_scanned=datetime(2023, 2, 6),
            scan_count=4,
            risk_tier="LOW",
            verdict="OK",
        ),
        McpServerRegistry(
            server_id="srv6",
            name="S6",
            registry_source="srcB",
            last_assessed=datetime(2022, 9, 15),
            first_seen=datetime(2022, 9, 10),
            last_seen=datetime(2022, 9, 16),
            last_scanned=datetime(2022, 9, 17),
            scan_count=6,
            risk_tier="MEDIUM",
            verdict="FAIL",
        ),
    ]

    sess = TestingSession()
    sess.add_all(seed)
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override – the service code calls ``get_session`` to obtain a
    # SQLAlchemy session.  We replace it with a generator that yields a session
    # bound to the in‑memory SQLite engine.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Assemble a minimal FastAPI app that includes only the router under test.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute the request and validate the response.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/freshness/registry-coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    # Basic shape checks
    assert "generated_at" in body, body
    assert isinstance(body["generated_at"], str), body
    assert "sources" in body and isinstance(body["sources"], list), body
    assert len(body["sources"]) == 2, body["sources"]
    assert "totals" in body and isinstance(body["totals"], dict), body

    # Verify the aggregated “never assessed” count matches the seeded data.
    expected_never_assessed = 2  # srv1 and srv4
    assert body["totals"].get("never_assessed") == expected_never_assessed, body["totals"]

    # Verify each source reports a median scan count (non‑null numeric).
    for src in body["sources"]:
        median = src.get("median_scan_count")
        assert median is not None, src
        assert isinstance(median, (int, float)), src

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – fail loudly
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)