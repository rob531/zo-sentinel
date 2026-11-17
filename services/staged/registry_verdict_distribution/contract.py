"""Acceptance self-test for the registry verdict distribution service."""

from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

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
    session.add(
        McpServerRegistry(
            server_id="registry-trusted",
            name="Trusted server",
            verdict="TRUSTED_GENERAL",
            risk_tier="LOW",
            trust_score=0.95,
            last_assessed=datetime.now(timezone.utc),
            registry_source="contract",
        )
    )
    session.add(
        McpServerRegistry(
            server_id="registry-isolated",
            name="Isolated server",
            verdict="HIGH_RISK_ISOLATED",
            risk_tier="HIGH",
            trust_score=0.15,
            last_assessed=datetime.now(timezone.utc),
            registry_source="contract",
        )
    )
    session.add(
        McpServerRegistry(
            server_id="registry-unknown",
            name="Unknown server",
            verdict=None,
            risk_tier="UNKNOWN",
            trust_score=0.5,
            last_assessed=datetime.now(timezone.utc),
            registry_source="contract",
        )
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
    expected_path = "/api/registry/verdict-distribution"
    router_paths = {getattr(route, "path", "") for route in router.routes}
    if expected_path not in router_paths:
        if "/registry/verdict-distribution" in router_paths:
            app.include_router(router, prefix="/api")
        else:
            app.include_router(router, prefix="/api/registry")
    else:
        app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get(expected_path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3, body
    assert body["by_verdict"]["TRUSTED_GENERAL"] == 1, body
    assert body["by_verdict"]["HIGH_RISK_ISOLATED"] == 1, body
    assert body["unknown_verdicts"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)