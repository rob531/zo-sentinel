"""Acceptance self-test for the threat-intel unmatched-indicators service."""
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
    from app.models import Base, McpServerRegistry, ThreatIntelRef

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    try:
        session.add(
            McpServerRegistry(
                server_id="srv-match",
                name="matched-server",
                url="https://matched.example",
                risk_tier="HIGH",
            )
        )
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="url",
                    indicator_value="https://matched.example",
                    pulse_id="pulse-1",
                    pulse_name="pulse one",
                    source="source-one",
                    source_url="https://source.example/one",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="url",
                    indicator_value="https://unmatched.example",
                    pulse_id="pulse-1",
                    pulse_name="pulse one",
                    source="source-one",
                    source_url="https://source.example/two",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="unmatched.example",
                    pulse_id="pulse-2",
                    pulse_name="pulse two",
                    source="source-two",
                    source_url="https://source.example/three",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()
    finally:
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

    with TestClient(app) as client:
        response = client.get("/api/threat-intel/unmatched-indicators?limit=20")
        assert response.status_code == 200, response.text
        body = response.json()

    by_type = {item["indicator_type"]: item for item in body["by_type"]}
    assert by_type["url"]["total"] == 2, body
    assert by_type["domain"]["total"] == 1, body
    assert by_type["url"]["unmatched"] >= 1, body
    assert by_type["domain"]["unmatched"] >= 1, body
    assert body["recent_unmatched"], body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)