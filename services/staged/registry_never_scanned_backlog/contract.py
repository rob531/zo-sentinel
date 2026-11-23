"""Contract self-test for the registry never-scanned backlog service."""
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
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="unscanned-github",
                name="Unscanned GitHub Server",
                registry_source="github",
                risk_tier="HIGH",
                scan_count=0,
                last_scanned=None,
                first_seen=datetime(2024, 1, 1, tzinfo=timezone.utc),
                last_seen=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
            McpServerRegistry(
                server_id="unscanned-npm",
                name="Unscanned NPM Server",
                registry_source="npm",
                risk_tier="LOW",
                scan_count=0,
                last_scanned=None,
                first_seen=datetime(2024, 1, 2, tzinfo=timezone.utc),
                last_seen=datetime(2024, 1, 4, tzinfo=timezone.utc),
            ),
            McpServerRegistry(
                server_id="scanned-after-registration",
                name="Recently Scanned Server",
                registry_source="github",
                risk_tier="MEDIUM",
                scan_count=0,
                last_scanned=datetime(2024, 1, 5, tzinfo=timezone.utc),
                first_seen=datetime(2024, 1, 5, tzinfo=timezone.utc),
                last_seen=datetime(2024, 1, 6, tzinfo=timezone.utc),
            ),
            McpServerRegistry(
                server_id="fully-scanned-one",
                name="Fully Scanned Server One",
                registry_source="npm",
                risk_tier="HIGH",
                scan_count=1,
                last_scanned=datetime(2024, 1, 7, tzinfo=timezone.utc),
                first_seen=datetime(2024, 1, 7, tzinfo=timezone.utc),
                last_seen=datetime(2024, 1, 8, tzinfo=timezone.utc),
            ),
            McpServerRegistry(
                server_id="fully-scanned-two",
                name="Fully Scanned Server Two",
                registry_source="github",
                risk_tier="LOW",
                scan_count=4,
                last_scanned=datetime(2024, 1, 9, tzinfo=timezone.utc),
                first_seen=datetime(2024, 1, 9, tzinfo=timezone.utc),
                last_seen=datetime(2024, 1, 10, tzinfo=timezone.utc),
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
    route_paths = [getattr(route, "path", "") for route in router.routes]
    prefix = "" if any(path.startswith("/api/") for path in route_paths) else "/api"
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/registry/never-scanned-backlog")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 2, body
    assert body["by_source"] == {"github": 1, "npm": 1}, body
    assert body["by_tier"] == {"HIGH": 1, "LOW": 1}, body
    assert len(body["sample"]) <= 50, body
    assert body["generated_at"], body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)