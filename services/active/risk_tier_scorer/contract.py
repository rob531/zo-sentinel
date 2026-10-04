"""contract.py -- liveness gate for risk_tier_scorer."""
from __future__ import annotations

import sys
from datetime import datetime, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)

    s = TestingSession()
    s.add(McpServerRegistry(
        server_id="contract-srv1",
        name="Contract Server 1",
        registry_source="test",
        url="http://example.com/c1",
        description="Contract test 1",
        confidence=0.9,
        first_seen=now,
        last_seen=now,
        last_scanned=now,
        last_assessed=now,
        meta={},
        scan_count=1,
        trust_score=0.8,
        verdict="clean",
        verdict_reasoning="none",
        risk_tier="low",
    ))
    s.add(McpLlmAxisScore(
        id="score-ax1-001",
        server_id="contract-srv1",
        axis_name="overall_risk",
        label="LOW",
        label_index=2,
        probs="[]",
        p_top=0.85,
        p_critical=0.05,
        p_danger=0.10,
        escalated=False,
        decision_rule_version="v1",
        model_version="v1",
        adapter_sha256="abc",
        scored_at=now,
    ))
    s.add(McpLlmAxisScore(
        id="score-ax2-002",
        server_id="contract-srv1",
        axis_name="auth_strength",
        label="HIGH",
        label_index=8,
        probs="[]",
        p_top=0.20,
        p_critical=0.50,
        p_danger=0.30,
        escalated=True,
        decision_rule_version="v1",
        model_version="v1",
        adapter_sha256="abc",
        scored_at=now,
    ))
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

    c = TestClient(app)

    # Single-server endpoint
    r = c.get("/api/risk_tier_scorer/contract-srv1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["server_id"] == "contract-srv1"
    assert body["server_name"] == "Contract Server 1"
    assert isinstance(body["overall_score"], float)
    assert body["risk_tier"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "UNKNOWN")
    assert len(body["axes"]) == 2
    assert body["evidence"]["axes_analyzed"] == 2

    # Query-param variant
    r2 = c.get("/api/risk_tier_scorer?server_id=contract-srv1")
    assert r2.status_code == 200, r2.text

    # Batch endpoint
    r3 = c.post("/api/risk_tier_scorer/batch", json={"server_ids": ["contract-srv1"]})
    assert r3.status_code == 200, r3.text
    batch_body = r3.json()
    assert isinstance(batch_body, list)
    assert len(batch_body) == 1
    assert batch_body[0]["server_id"] == "contract-srv1"

    # Unknown server returns UNKNOWN tier, not 404
    r4 = c.get("/api/risk_tier_scorer/no-such-server")
    assert r4.status_code == 200, r4.text
    assert r4.json()["risk_tier"] == "UNKNOWN"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)
