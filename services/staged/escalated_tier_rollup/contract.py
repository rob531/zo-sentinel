"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite database.
If the service boots, mounts, serves 200 and returns a schema‑valid body,
the script prints ``PASS`` and exits 0. Any exception causes ``FAIL`` and a
non‑zero exit.
"""
from __future__ import annotations

import sys

def run() -> bool:
    """Boot the service with a test DB, seed data, and verify the endpoint."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer imports – required for a non‑hollow build
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpLlmAxisScore

    # Router that implements the endpoint under test
    from .router import router

    # ------------------------------------------------------------------ #
    # Set up an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed minimal data required by the service logic
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    # Servers
    sess.add_all([
        McpServerRegistry(server_id="srv1", name="A", risk_tier="HIGH"),
        McpServerRegistry(server_id="srv2", name="B", risk_tier="HIGH"),
        McpServerRegistry(server_id="srv3", name="C", risk_tier="LOW"),
    ])

    # Axis scores – two decision_rule_versions, three rows, two escalated
    sess.add_all([
        McpLlmAxisScore(
            id=1,
            server_id="srv1",
            axis_name="cpu",
            label="low",
            label_index=0,
            probs="{}",
            p_top=0.6,
            p_critical=0.2,
            p_danger=0.2,
            escalated=True,
            escalated_to="tier2",
            decision_rule_version="v1",
            model_version="1.0",
            adapter_sha256="a"*64,
        ),
        McpLlmAxisScore(
            id=2,
            server_id="srv2",
            axis_name="cpu",
            label="low",
            label_index=0,
            probs="{}",
            p_top=0.7,
            p_critical=0.15,
            p_danger=0.15,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="1.0",
            adapter_sha256="b"*64,
        ),
        McpLlmAxisScore(
            id=3,
            server_id="srv3",
            axis_name="memory",
            label="high",
            label_index=1,
            probs="{}",
            p_top=0.5,
            p_critical=0.3,
            p_danger=0.2,
            escalated=True,
            escalated_to="tier3",
            decision_rule_version="v2",
            model_version="1.0",
            adapter_sha256="c"*64,
        ),
    ])
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override so the service uses the test session
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute the request against the test client and assert expectations
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/scores/escalated-tier-rollup")
    assert response.status_code == 200, response.text
    body = response.json()

    # Total escalated rows must be 2 (as seeded)
    assert body["total_escalated"] == 2, body

    # Verify a known grouping appears with count 1
    cpu_group = next(
        (g for g in body["by_axis"]
         if g["axis_name"] == "cpu"
         and g["escalated_to"] == "tier2"
         and g["decision_rule_version"] == "v1"),
        None,
    )
    assert cpu_group is not None, body["by_axis"]
    assert cpu_group["count"] == 1, cpu_group

    # Verify escalation rate for the 'cpu' axis is between 0.0 and 1.0
    cpu_rate = next(
        (r for r in body["escalation_rate"] if r["axis_name"] == "cpu"),
        None,
    )
    assert cpu_rate is not None, body["escalation_rate"]
    assert cpu_rate["escalated"] == 1, cpu_rate
    assert cpu_rate["total"] == 2, cpu_rate
    assert 0.0 <= cpu_rate["rate"] <= 1.0, cpu_rate

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)