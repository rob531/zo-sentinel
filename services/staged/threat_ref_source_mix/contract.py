"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.staged.threat_ref_source_mix.contract` -- and a zero exit is
the proof that the service boots, mounts, serves 200, and returns a schema-valid
body over the REAL data layer (overridden to an in-memory SQLite for hermeticity).
A non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip.

Run:  python -m services.staged.threat_ref_source_mix.contract
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
    try:
        Base.metadata.create_all(eng)
    except Exception:  # noqa: BLE001 -- narrow DDL to the table under test
        ThreatIntelRef.__table__.create(eng, checkfirst=True)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    stale = now - timedelta(days=10)

    s = TestingSession()
    s.add(ThreatIntelRef(
        indicator_type="ip",
        indicator_value="1.1.1.1",
        pulse_id="p1",
        pulse_name="Alpha Pulse",
        source="alpha",
        source_url="https://alpha.example/a",
        is_aggregator=False,
        fetched_at=now,
    ))
    s.add(ThreatIntelRef(
        indicator_type="ip",
        indicator_value="2.2.2.2",
        pulse_id="p1",
        pulse_name="Alpha Pulse",
        source="alpha",
        source_url="https://alpha.example/b",
        is_aggregator=False,
        fetched_at=now,
    ))
    s.add(ThreatIntelRef(
        indicator_type="domain",
        indicator_value="a.example",
        pulse_id="p2",
        pulse_name="Alpha Pulse Two",
        source="alpha",
        source_url="https://alpha.example/c",
        is_aggregator=False,
        fetched_at=now,
    ))
    s.add(ThreatIntelRef(
        indicator_type="ip",
        indicator_value="3.3.3.3",
        pulse_id="p3",
        pulse_name="Beta Pulse",
        source="beta",
        source_url="https://beta.example/a",
        is_aggregator=True,
        fetched_at=now,
    ))
    s.add(ThreatIntelRef(
        indicator_type="domain",
        indicator_value="b.example",
        pulse_id="p4",
        pulse_name="Beta Pulse Two",
        source="beta",
        source_url="https://beta.example/b",
        is_aggregator=False,
        fetched_at=stale,
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
    r = c.get("/api/threat-refs/source-mix")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total_rows"] == 5, body
    assert body["aggregator_rows"] == 1, body
    assert body["stale_rows"] == 1, body

    by_source = body["by_source"]
    assert len(by_source) == 2, body
    assert sum(int(x["indicators"]) for x in by_source) == 5, body
    assert sum(int(x["pulses"]) for x in by_source) == 4, body

    by_type = body["by_indicator_type"]
    assert len(by_type) == 2, body
    assert sum(int(x["count"]) for x in by_type) == 5, body

    assert "max_age_hours" in body, body
    assert "generated_at" in body, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)