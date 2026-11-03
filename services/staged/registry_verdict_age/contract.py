"""Acceptance self-test for the registry verdict-age service.

Run with ``python -m services.staged.registry_verdict_age.contract``.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta


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
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    today = date.today()
    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="verdict-age-2d",
                    verdict="ALLOW",
                    risk_tier="LOW",
                    last_assessed=today - timedelta(days=2),
                    last_seen=today - timedelta(days=2),
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="verdict-age-10d",
                    verdict="BLOCK",
                    risk_tier="HIGH",
                    last_assessed=today - timedelta(days=10),
                    last_seen=today - timedelta(days=10),
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="verdict-age-45d",
                    verdict="REVIEW",
                    risk_tier="MEDIUM",
                    last_assessed=today - timedelta(days=45),
                    last_seen=today - timedelta(days=45),
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="verdict-age-200d",
                    verdict="ALLOW",
                    risk_tier="HIGH",
                    last_assessed=today - timedelta(days=200),
                    last_seen=today - timedelta(days=200),
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="verdict-age-never",
                    verdict="UNKNOWN",
                    risk_tier="LOW",
                    last_assessed=None,
                    last_seen=today,
                    registry_source="contract",
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

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get(
        "/api/registry/verdict-age",
        params={"days": 3650},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 3650, body
    assert body["total"] == 5, body
    assert body["never_assessed"] == 1, body

    actual_counts = {
        (row["band"], row["verdict"], row["risk_tier"]): row["count"]
        for row in body["bands"]
    }
    expected_counts = {
        ("0-7", "ALLOW", "LOW"): 1,
        ("8-30", "BLOCK", "HIGH"): 1,
        ("31-90", "REVIEW", "MEDIUM"): 1,
        ("91-365", "ALLOW", "HIGH"): 1,
    }
    for key, expected_count in expected_counts.items():
        assert actual_counts.get(key) == expected_count, body

    assert len({row["band"] for row in body["bands"]}) >= 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)