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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = testing_session()
    session.add(
        McpServerRegistry(
            server_id="fresh-server",
            name="Fresh Server",
            registry_source="registry",
            trust_score=0.9,
            verdict="trusted",
            confidence=0.95,
            risk_tier="LOW",
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=now - timedelta(days=1),
            last_assessed=now,
            scan_count=3,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="stale-server",
            name="Stale Server",
            registry_source="registry",
            trust_score=0.5,
            verdict="unknown",
            confidence=0.5,
            risk_tier="MEDIUM",
            first_seen=now - timedelta(days=50),
            last_seen=now,
            last_scanned=now - timedelta(days=40),
            last_assessed=now - timedelta(days=40),
            scan_count=1,
        )
    )
    session.commit()
    session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    expected_keys = {
        "server_id",
        "name",
        "registry_source",
        "trust_score",
        "verdict",
        "confidence",
        "risk_tier",
        "first_seen",
        "last_seen",
        "last_scanned",
        "last_assessed",
        "scan_count",
        "scan_stale",
        "days_since_seen",
    }

    response = client.get("/api/servers/fresh-server/lifecycle")
    assert response.status_code == 200, response.text
    fresh = response.json()
    assert fresh["scan_stale"] is False, fresh
    assert fresh["scan_count"] == 3, fresh
    assert set(fresh) == expected_keys, fresh

    response = client.get("/api/servers/stale-server/lifecycle")
    assert response.status_code == 200, response.text
    stale = response.json()
    assert stale["scan_stale"] is True, stale
    assert stale["scan_count"] == 1, stale
    assert set(stale) == expected_keys, stale

    response = client.get("/api/servers/unknown-server/lifecycle")
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "server not found"}, response.json()

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)