from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

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
    s.add(
        McpServerRegistry(
            server_id="srv-never",
            name="Never scanned",
            registry_source="test",
            last_scanned=None,
            scan_count=0,
            risk_tier="HIGH",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv-stale",
            name="Scanned forty days ago",
            registry_source="test",
            last_scanned=now - timedelta(days=40),
            scan_count=2,
            risk_tier="MEDIUM",
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv-fresh",
            name="Scanned today",
            registry_source="test",
            last_scanned=now,
            scan_count=3,
            risk_tier="LOW",
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
    response = client.get("/api/servers/scan-cadence")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "generated_at" in body, body
    assert "buckets" in body, body
    assert "totals" in body, body

    totals = body["totals"]
    assert totals["never_scanned"] == 1, body
    assert totals["stale_30d"] == 1, body
    assert totals["fresh"] == 1, body
    assert totals["total"] == 3, body

    buckets = body["buckets"]
    assert isinstance(buckets, list), body
    test_bucket = next(
        (bucket for bucket in buckets if bucket.get("source") == "test"),
        None,
    )
    assert test_bucket is not None, body
    assert test_bucket["never_scanned"] == 1, body
    assert test_bucket["stale_30d"] == 1, body
    assert test_bucket["fresh"] == 1, body
    assert test_bucket["total"] == 3, body
    assert test_bucket["avg_scan_count"] == (0 + 2 + 3) / 3, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)