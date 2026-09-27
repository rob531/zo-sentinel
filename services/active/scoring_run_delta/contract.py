"""contract.py -- self-test for scoring_run_delta.

Run:  python -m services.active.scoring_run_delta.contract  ->  PASS / FAIL
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore

from .router import router


def run() -> bool:
    # In-memory SQLite for hermetic testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed two servers with 3 scores each (oldest -> newest)
    db = TestingSession()
    now = datetime.utcnow()
    d1 = timedelta(days=1)
    d2 = timedelta(days=2)
    d3 = timedelta(days=3)

    # server-a: security axis rises 0.2 -> 0.4 -> 0.6 (direction change: medium->high)
    for i, (pt, off, esc) in enumerate([(0.2, d3, False), (0.4, d2, False), (0.6, d1, True)]):
        db.add(McpLlmAxisScore(
            server_id="server-a",
            axis_name="security",
            p_top=pt,
            escalated=esc,
            scored_at=now - off,
            model_version="v1",
            adapter_sha256="sha",
            decision_rule_version="r1",
            label="test",
            label_index=0,
        ))
    # server-a: reliability stays flat 0.5 -> 0.51 -> 0.52
    for i, (pt, off, esc) in enumerate([(0.5, d3, False), (0.51, d2, False), (0.52, d1, False)]):
        db.add(McpLlmAxisScore(
            server_id="server-a",
            axis_name="reliability",
            p_top=pt,
            escalated=esc,
            scored_at=now - off,
            model_version="v1",
            adapter_sha256="sha",
            decision_rule_version="r1",
            label="test",
            label_index=0,
        ))
    # server-b: one score only -> should be skipped (need 2+)
    db.add(McpLlmAxisScore(
        server_id="server-b",
        axis_name="safety",
        p_top=0.8,
        escalated=False,
        scored_at=now - d1,
        model_version="v1",
        adapter_sha256="sha",
        decision_rule_version="r1",
        label="test",
        label_index=0,
    ))
    db.commit()
    db.close()

    client = TestClient(app)

    # Happy path: server-a has deltas
    r = client.get("/api/scoring/run/delta?server_id=server-a&days=10")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "axes" in body, f"Missing 'axes' in response: {body}"
    assert len(body["axes"]) >= 1, f"Expected at least 1 axis delta for server-a, got {body}"

    # Verify security axis (oldest=0.2, newest=0.6, delta=0.4)
    sec = next((a for a in body["axes"] if a["axis_name"] == "security"), None)
    assert sec is not None, f"security axis not found in {body['axes']}"
    assert sec["p_top_before"] == 0.2, sec
    assert sec["p_top_after"] == 0.6, sec
    assert sec["delta"] == 0.4, sec
    assert sec["direction_changed"] is True, sec  # 0.2(low) -> 0.6(high)
    assert sec["escalated"] is True, sec  # False -> True

    # Reliability should have delta but no direction change or escalation
    rel = next((a for a in body["axes"] if a["axis_name"] == "reliability"), None)
    assert rel is not None, f"reliability axis not found"
    assert rel["p_top_before"] == 0.5, rel
    assert rel["p_top_after"] == 0.52, rel
    assert abs(rel["delta"] - 0.02) < 0.001, rel
    assert rel["direction_changed"] is False, rel
    assert rel["escalated"] is False, rel

    # Totals
    assert body["runs_compared"] == 2, body
    assert body["total_direction_changes"] == 1, body
    assert body["total_escalated_axes"] == 1, body
    assert body["server_id"] == "server-a", body
    assert body["days"] == 10, body

    # Server with only one score returns empty axes
    r2 = client.get("/api/scoring/run/delta?server_id=server-b&days=10")
    assert r2.status_code == 200
    assert r2.json()["axes"] == [], r2.json()

    # Unknown server returns empty
    r3 = client.get("/api/scoring/run/delta?server_id=does-not-exist&days=10")
    assert r3.status_code == 200
    assert r3.json()["axes"] == [], r3.json()

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)
