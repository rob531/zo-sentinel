"""Acceptance self-test for the registry verdict ageing board."""
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
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-never",
                name="Never assessed",
                risk_tier="LOW",
                registry_source="manual",
                verdict="unknown",
                last_assessed=None,
                last_seen=now,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="srv-fresh",
                name="Fresh verdict",
                risk_tier="MEDIUM",
                registry_source="github",
                verdict="safe",
                last_assessed=now - timedelta(hours=2),
                last_seen=now,
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="srv-45d",
                name="Forty-five day verdict",
                risk_tier="MEDIUM",
                registry_source="github",
                verdict="caution",
                last_assessed=now - timedelta(days=45),
                last_seen=now,
                scan_count=3,
            ),
            McpServerRegistry(
                server_id="srv-200d",
                name="Two hundred day verdict",
                risk_tier="HIGH",
                registry_source="registry",
                verdict="unsafe",
                last_assessed=now - timedelta(days=200),
                last_seen=now,
                scan_count=4,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/registry/verdict-ageing")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "buckets" in body, body
        assert "total" in body, body
        assert body["total"] == 4, body

        buckets = {bucket["label"]: bucket for bucket in body["buckets"]}
        assert buckets["never"]["count"] == 1, body
        assert buckets["90d+"]["count"] == 1, body
        assert buckets["90d+"]["by_tier"]["HIGH"] == 1, body

        for bucket in body["buckets"]:
            assert sum(bucket["by_tier"].values()) == bucket["count"], body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)