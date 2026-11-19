"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.staged.escalation_history_api.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.escalation_history_api.contract   ->  prints PASS / FAIL, exits 0/1
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    s = TestingSession()
    s.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="srv-1",
                axis_name="security",
                label="critical finding",
                p_top=0.98,
                p_critical=0.91,
                p_danger=0.87,
                escalated=True,
                escalated_to="HIGH",
                scored_at=now,
                decision_rule_version="rules-v1",
                model_version="model-v1",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-2",
                axis_name="security",
                label="important finding",
                p_top=0.88,
                p_critical=0.73,
                p_danger=0.69,
                escalated=True,
                escalated_to="MEDIUM",
                scored_at=now - timedelta(hours=1),
                decision_rule_version="rules-v1",
                model_version="model-v1",
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv-3",
                axis_name="quality",
                label="quality finding",
                p_top=0.77,
                p_critical=0.61,
                p_danger=0.55,
                escalated=True,
                escalated_to="HIGH",
                scored_at=now - timedelta(hours=2),
                decision_rule_version="rules-v1",
                model_version="model-v1",
            ),
            McpLlmAxisScore(
                id=4,
                server_id="srv-4",
                axis_name="quality",
                label="not escalated",
                p_top=0.21,
                p_critical=0.12,
                p_danger=0.09,
                escalated=False,
                escalated_to=None,
                scored_at=now,
                decision_rule_version="rules-v1",
                model_version="model-v1",
            ),
        ]
    )
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
    r = c.get("/api/audit/escalations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3, body
    assert len(body["by_axis"]) == 2, body
    assert len(body["escalations"]) == 3, body
    assert all(item.get("escalated_to") for item in body["escalations"]), body
    assert all(item.get("label") != "not escalated" for item in body["escalations"]), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)