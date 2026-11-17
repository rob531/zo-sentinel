"""Acceptance self-test for the tier age distribution board."""
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
    from app.models import McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="tier-age-now-high",
                    name="Tier Age Now High",
                    risk_tier="HIGH",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="tier-age-three-days-high",
                    name="Tier Age Three Days High",
                    risk_tier="HIGH",
                    last_assessed=now - timedelta(days=3),
                ),
                McpServerRegistry(
                    server_id="tier-age-ten-days-high",
                    name="Tier Age Ten Days High",
                    risk_tier="HIGH",
                    last_assessed=now - timedelta(days=10),
                ),
                McpServerRegistry(
                    server_id="tier-age-sixty-days-low",
                    name="Tier Age Sixty Days Low",
                    risk_tier="LOW",
                    last_assessed=now - timedelta(days=60),
                ),
                McpServerRegistry(
                    server_id="tier-age-never-low",
                    name="Tier Age Never Low",
                    risk_tier="LOW",
                    last_assessed=None,
                ),
                McpServerRegistry(
                    server_id="tier-age-now-low",
                    name="Tier Age Now Low",
                    risk_tier="LOW",
                    last_assessed=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get("/api/registry/tier-age")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["buckets"] == ["0_1d", "1_7d", "7_30d", "30d_plus", "never"], body
    assert body["tiers"]["HIGH"]["0_1d"] == 1, body
    assert body["tiers"]["HIGH"]["1_7d"] == 1, body
    assert body["tiers"]["HIGH"]["7_30d"] == 1, body
    assert body["tiers"]["HIGH"]["count"] == 3, body
    assert body["tiers"]["LOW"]["0_1d"] == 1, body
    assert body["tiers"]["LOW"]["30d_plus"] == 1, body
    assert body["tiers"]["LOW"]["never"] >= 1, body
    assert body["tiers"]["LOW"]["count"] == 3, body
    assert body["totals"]["scored"] == 5, body
    assert body["totals"]["never_scored"] == 1, body
    assert body["totals"]["by_tier"] == {"HIGH": 3, "LOW": 3}, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)