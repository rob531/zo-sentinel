"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.staged.dispute_timeline.contract` -- and a zero exit is the
proof that the service boots, mounts, serves 200, and returns a schema-valid
body over the REAL data layer (overridden to an in-memory SQLite for
hermeticity). A non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not
a silent skip.

Run:  python -m services.staged.dispute_timeline.contract -> prints PASS / FAIL
"""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpScoreDispute, McpServerRegistry

    from .router import router

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day0 = now.replace(hour=12, minute=0, second=0, microsecond=0)
    day1 = day0 - timedelta(days=1)

    s = TestingSession()
    s.add(McpServerRegistry(server_id="srv-dt-1", name="Alpha", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv-dt-2", name="Beta", risk_tier="LOW"))

    s.add(McpScoreDispute(
        server_id="srv-dt-1", proposed_overall_risk=0.9, reason_category="axis_conflict",
        status="open", submitted_by="reviewer@example.com", explanation="risk mismatch",
        created_at=day0))
    s.add(McpScoreDispute(
        server_id="srv-dt-2", proposed_overall_risk=0.4, reason_category="stale_scan",
        status="open", submitted_by="reviewer@example.com", explanation="stale evidence",
        created_at=day0))
    s.add(McpScoreDispute(
        server_id="srv-dt-1", proposed_overall_risk=0.7, reason_category="axis_conflict",
        status="resolved", submitted_by="reviewer@example.com", explanation="resolved after review",
        created_at=day1, resolved_at=day1 + timedelta(hours=2)))
    s.add(McpScoreDispute(
        server_id="srv-dt-2", proposed_overall_risk=0.2, reason_category="policy_gap",
        status="resolved", submitted_by="reviewer@example.com", explanation="policy gap closed",
        created_at=day1, resolved_at=day1 + timedelta(hours=3)))
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
    r = None
    for path in ("/api/disputes/timeline?days=30", "/disputes/timeline?days=30"):
        cand = c.get(path)
        if cand.status_code == 200:
            r = cand
            break
    assert r is not None, "no 200 response for /api/disputes/timeline"
    body = r.json()

    assert body["days"] == 30, body
    assert body["total"] == 4, body

    by_status = body["by_status"]
    assert set(by_status.keys()) == {"open", "resolved"}, body
    assert by_status["open"] == 2, body
    assert by_status["resolved"] == 2, body

    series = body["series"]
    assert isinstance(series, list) and series, body
    assert all("date" in p and "count" in p for p in series), body
    assert sum(p["count"] for p in series) == 4, body
    assert any(p["count"] == 2 for p in series), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)