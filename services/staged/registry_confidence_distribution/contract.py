"""Acceptance self-test for the registry confidence distribution service."""
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
    from app.models import McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    source = "seeded_registry_source"
    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-srv-1",
                    name="Contract Server One",
                    registry_source=source,
                    verdict="trusted",
                    confidence=0.15,
                    trust_score=15,
                    risk_tier="LOW",
                    last_assessed=now,
                    last_scanned=now,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="contract-srv-2",
                    name="Contract Server Two",
                    registry_source=source,
                    verdict="trusted",
                    confidence=0.85,
                    trust_score=85,
                    risk_tier="HIGH",
                    last_assessed=now,
                    last_scanned=now,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="contract-srv-3",
                    name="Contract Server Three",
                    registry_source=source,
                    verdict="unknown",
                    confidence=None,
                    trust_score=None,
                    risk_tier="MEDIUM",
                    last_assessed=now,
                    last_scanned=now,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="contract-srv-4",
                    name="Contract Server Four",
                    registry_source=source,
                    verdict="unknown",
                    confidence=0.50,
                    trust_score=50,
                    risk_tier="UNKNOWN",
                    last_assessed=None,
                    last_scanned=None,
                    scan_count=0,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/confidence-distribution")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_servers"] == 4, body
    assert sum(body["confidence_histogram"].values()) == 4, body
    assert body["confidence_histogram"].get("unknown") == 1, body
    assert body["unassessed_count"] == 1, body
    assert body["assessed_count"] == 3, body
    assert source in body["by_registry_source"], body
    assert body["by_registry_source"][source] == 4, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)