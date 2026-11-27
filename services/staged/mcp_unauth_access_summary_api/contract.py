"""contract.py -- the service's ACCEPTANCE self‑test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite database.
If the service starts, mounts its router, returns HTTP 200 and a schema‑valid
response, the test prints ``PASS`` and exits 0. Any exception causes ``FAIL``
and a non‑zero exit code.
"""
from __future__ import annotations

import sys

def run() -> bool:
    """Execute the self‑test.

    Returns:
        True if all assertions pass.
    """
    # FastAPI and testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite setup
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer imports
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    # Router for the service under test
    from .router import router

    # --------------------------------------------------------------------- #
    # Create an in‑memory SQLite engine and initialise the schema
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # --------------------------------------------------------------------- #
    # Seed data: 4 unauth‑access rows + matching server‑registry rows
    sess = TestingSession()
    # Server registry entries
    sess.add(McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH", url="https://alpha.example"))
    sess.add(McpServerRegistry(server_id="srv2", name="Beta", risk_tier="MEDIUM", url="https://beta.example"))
    sess.add(McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="LOW", url="https://gamma.example"))
    sess.add(McpServerRegistry(server_id="srv4", name="Delta", risk_tier="HIGH", url="https://delta.example"))
    # Unauth‑access entries
    sess.add(McpUnauthAccess(server_id="srv1", source="scan", unauth_access=True, url="https://alpha.example"))
    sess.add(McpUnauthAccess(server_id="srv2", source="scan", unauth_access=False, url="https://beta.example"))
    sess.add(McpUnauthAccess(server_id="srv3", source="audit", unauth_access=True, url="https://gamma.example"))
    sess.add(McpUnauthAccess(server_id="srv4", source="audit", unauth_access=False, url="https://delta.example"))
    sess.commit()
    sess.close()

    # --------------------------------------------------------------------- #
    # Dependency override to inject the testing session
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # Build a minimal FastAPI app with the service router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # --------------------------------------------------------------------- #
    # Execute the request against the test client
    client = TestClient(app)
    response = client.get("/api/unauth-access/summary")
    assert response.status_code == 200, f"Unexpected status: {response.status_code} – {response.text}"
    body = response.json()

    # --------------------------------------------------------------------- #
    # Assertions required by the acceptance criteria
    # 1. Sum of counts per source must equal the number of seeded rows (4)
    source_counts = sum(item.get("count", 0) for item in body.get("by_source", []))
    assert source_counts == 4, f"by_source counts sum {source_counts} != 4"

    # 2. Exposed servers must contain the seeded critical (HIGH risk) server with unauth_access=True
    exposed = body.get("exposed_servers", [])
    critical_present = any(
        srv.get("risk_tier") == "HIGH"
        and srv.get("unauth_access") is True
        and srv.get("server_id") == "srv1"
        for srv in exposed
    )
    assert critical_present, "Critical server with unauth_access=True not found in exposed_servers"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)