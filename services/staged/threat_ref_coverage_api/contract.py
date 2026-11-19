"""Acceptance self-test for the threat reference coverage API."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, ThreatIntelRef

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-alpha",
                name="Alpha",
                risk_tier="HIGH",
                url="https://alpha.example",
            ),
            McpServerRegistry(
                server_id="srv-beta",
                name="Beta",
                risk_tier="LOW",
                url="https://beta.example",
            ),
        ]
    )
    session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="url",
                indicator_value="https://alpha.example",
                pulse_id="pulse-1",
                pulse_name="Alpha pulse",
                source="alpha-source",
                source_url="https://alpha.example/source",
                is_aggregator=False,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="url",
                indicator_value="https://beta.example",
                pulse_id="pulse-1",
                pulse_name="Alpha pulse",
                source="beta-source",
                source_url="https://beta.example/source",
                is_aggregator=True,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="server_id",
                indicator_value="srv-alpha",
                pulse_id="pulse-2",
                pulse_name="Beta pulse",
                source="alpha-source",
                source_url="https://alpha.example/source",
                is_aggregator=False,
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="server_id",
                indicator_value="srv-beta",
                pulse_id="pulse-2",
                pulse_name="Beta pulse",
                source="beta-source",
                source_url="https://beta.example/source",
                is_aggregator=False,
            ),
            ThreatIntelRef(
                id=5,
                indicator_type="url",
                indicator_value="https://alpha.example",
                pulse_id="pulse-2",
                pulse_name="Beta pulse",
                source="alpha-source",
                source_url="https://alpha.example/source",
                is_aggregator=False,
            ),
        ]
    )
    session.commit()
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

    client = TestClient(app)
    response = client.get("/api/threat-refs/coverage", params={"window_days": 30})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["window_days"] == 30, body
    assert body["total_refs"] == 5, body
    assert body["by_indicator_type"] == {"url": 3, "server_id": 2}, body
    assert body["by_source"] == {"alpha-source": 3, "beta-source": 2}, body
    assert body["aggregator_count"] == 1, body
    assert body["pulses"], body
    assert all("ref_count" in pulse for pulse in body["pulses"]), body
    assert isinstance(body["coverage_ratio"], float), body
    assert 0.0 <= body["coverage_ratio"] <= 1.0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)