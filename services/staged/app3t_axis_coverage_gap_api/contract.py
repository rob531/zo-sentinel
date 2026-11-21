"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite database,
mounts the router, and asserts that the endpoint returns the expected schema
and values.
"""
from __future__ import annotations

import sys
from typing import Generator

def run() -> bool:
    """Execute the self‑test.

    Returns:
        True if all assertions pass.
    """
    # FastAPI and testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy in‑memory engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real application data layer
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpLlmAxisScore

    # Router under test
    from .router import router

    # --------------------------------------------------------------------- #
    # Set up an in‑memory SQLite database that mirrors the real schema.
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # --------------------------------------------------------------------- #
    # Seed data:
    #   * 3 servers (srv1, srv2, srv3)
    #   * 7 axes: overall_risk, auth_strength, capability_breadth,
    #     data_sensitivity, network_egress, maintainer_trust, exploit_surface
    #   * srv1 has scores for all axes
    #   * srv2 missing only exploit_surface
    #   * srv3 missing all axes
    # --------------------------------------------------------------------- #
    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]

    sess = TestingSession()
    # Servers
    sess.add(McpServerRegistry(server_id="srv1", name="Server 1", risk_tier="HIGH"))
    sess.add(McpServerRegistry(server_id="srv2", name="Server 2", risk_tier="MEDIUM"))
    sess.add(McpServerRegistry(server_id="srv3", name="Server 3", risk_tier="LOW"))
    sess.commit()

    # Scores for srv1 (all axes)
    for i, axis in enumerate(axes, start=1):
        sess.add(
            McpLlmAxisScore(
                id=f"srv1-{axis}",
                server_id="srv1",
                axis_name=axis,
                model_version="v1",
                # Minimal required fields; others left nullable/default.
            )
        )
    # Scores for srv2 (all except exploit_surface)
    for axis in axes:
        if axis == "exploit_surface":
            continue
        sess.add(
            McpLlmAxisScore(
                id=f"srv2-{axis}",
                server_id="srv2",
                axis_name=axis,
                model_version="v1",
            )
        )
    sess.commit()
    sess.close()

    # --------------------------------------------------------------------- #
    # Dependency override that yields a fresh session bound to the in‑memory DB.
    # --------------------------------------------------------------------- #
    def _override() -> Generator[TestingSession, None, None]:
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Build FastAPI app, mount router, apply override.
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # --------------------------------------------------------------------- #
    # Execute request against the endpoint and validate response.
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/app3t/axis/coverage-gaps")
    assert response.status_code == 200, f"Unexpected status: {response.text}"
    body = response.json()

    # Basic shape checks
    assert isinstance(body, dict), f"Body not a dict: {body}"
    axes_resp = body.get("axes")
    assert isinstance(axes_resp, list) and len(axes_resp) == 7, f"Axes list invalid: {axes_resp}"
    totals = body.get("totals")
    assert isinstance(totals, dict), f"Totals missing: {body}"

    # Specific checks for exploit_surface
    exploit = next((a for a in axes_resp if a.get("axis_name") == "exploit_surface"), None)
    assert exploit is not None, "exploit_surface entry missing"
    assert exploit.get("missing_servers") == 2, f"Missing servers count wrong: {exploit}"
    coverage = exploit.get("coverage_pct")
    assert isinstance(coverage, (int, float)) and 0 <= coverage <= 100, f"Coverage pct out of range: {coverage}"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)