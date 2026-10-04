"""contract.py -- acceptance self-test for risk_tier_change_analysis."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

from .router import router


def run() -> bool:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    s = TestingSession()
    # Server escalated: was LOW, now HIGH
    s.add(McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH"))
    s.add(McpLlmAxisScore(
        server_id="srv1", axis_name="overall_risk", label="LOW",
        label_index=3, scored_at=now - timedelta(days=5), model_version="v1",
    ))
    # Server de-escalated: was HIGH, now LOW
    s.add(McpServerRegistry(server_id="srv2", name="Beta", risk_tier="LOW"))
    s.add(McpLlmAxisScore(
        server_id="srv2", axis_name="overall_risk", label="HIGH",
        label_index=1, scored_at=now - timedelta(days=7), model_version="v1",
    ))
    # Server stable: no historical change
    s.add(McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="MEDIUM"))
    s.add(McpLlmAxisScore(
        server_id="srv3", axis_name="overall_risk", label="MEDIUM",
        label_index=2, scored_at=now - timedelta(days=3), model_version="v1",
    ))
    # Server with no risk_tier (excluded)
    s.add(McpServerRegistry(server_id="srv4", name="Delta", risk_tier=None))
    s.commit()
    s.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Test summary endpoint
    r = client.get("/api/risk-tier-changes?days=30")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_servers"] == 3, f"Expected 3 servers, got {body}"
    assert body["escalated_count"] == 1, f"Expected 1 escalated, got {body}"
    assert body["de_escalated_count"] == 1, f"Expected 1 de-escalated, got {body}"
    assert body["changed_count"] == 2, f"Expected 2 changed, got {body}"

    # Test per-server endpoint
    r1 = client.get("/api/risk-tier-changes/srv1?days=30")
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["server_id"] == "srv1"
    assert d1["current_tier"] == "HIGH"
    assert d1["previous_tier"] == "LOW"
    assert d1["changed"] is True
    assert d1["change_direction"] == "escalated"

    r2 = client.get("/api/risk-tier-changes/srv2?days=30")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["change_direction"] == "de_escalated"

    r3 = client.get("/api/risk-tier-changes/srv3?days=30")
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert d3["changed"] is False

    # Non-existent server
    r404 = client.get("/api/risk-tier-changes/nonexistent?days=30")
    assert r404.status_code == 404, r404.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)
