"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.<stage>.<name>.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.threat_intel_coverage_service.contract
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
    from app.models import Base, ThreatIntelRef

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    s = TestingSession()
    s.add(
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.10",
            pulse_id="pulse-alpha-1",
            pulse_name="Alpha pulse",
            pulse_created=now,
            is_aggregator=False,
            source="alpha",
            source_url="https://alpha.example/indicators/192.0.2.10",
            fetched_at=now,
        )
    )
    s.add(
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id="pulse-alpha-2",
            pulse_name="Alpha pulse",
            pulse_created=yesterday,
            is_aggregator=False,
            source="alpha",
            source_url="https://alpha.example/indicators/example.org",
            fetched_at=yesterday,
        )
    )
    s.add(
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="198.51.100.20",
            pulse_id="pulse-beta-1",
            pulse_name="Beta pulse",
            pulse_created=now,
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/indicators/198.51.100.20",
            fetched_at=now,
        )
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

    client = TestClient(app)
    response = client.get("/api/threat-intel/coverage?days=30")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_indicators"] == 3, body
    assert len(body["daily"]) == 2, body
    assert any(
        item["source"] == "alpha" and item["count"] == 2
        for item in body["by_source"]
    ), body
    assert any(
        item["source"] == "alpha" and item["stale"] is False
        for item in body["by_source"]
    ), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)