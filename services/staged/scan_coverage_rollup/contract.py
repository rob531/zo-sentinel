"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Run: python -m services.staged.scan_coverage_rollup.contract
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
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="scan-srv-1",
                name="recently scanned",
                registry_source="source_a",
                scan_count=3,
                last_scanned=now - timedelta(days=1),
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="scan-srv-2",
                name="never scanned",
                registry_source="source_b",
                scan_count=0,
                last_scanned=None,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="scan-srv-3",
                name="stale scan",
                registry_source="source_c",
                scan_count=1,
                last_scanned=now - timedelta(days=10),
                risk_tier="MEDIUM",
            ),
        ]
    )
    session.commit()
    session.close()

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
    response = client.get("/api/coverage/scan-coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "sources" in body, body
    assert "totals" in body, body
    assert body["totals"]["total"] == 3, body
    assert body["totals"]["never_scanned"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)