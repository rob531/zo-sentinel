from __future__ import annotations

import sys
from datetime import datetime


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, ThreatIntelRef

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
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="aggregator-one.example",
                pulse_id="pulse-1",
                pulse_name="Aggregator Pulse One",
                pulse_created=now,
                is_aggregator=True,
                source="aggregator-feed-one",
                source_url="https://aggregator.example/one",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.10",
                pulse_id="pulse-2",
                pulse_name="Aggregator Pulse Two",
                pulse_created=now,
                is_aggregator=True,
                source="aggregator-feed-two",
                source_url="https://aggregator.example/two",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="direct-one.example",
                pulse_id="pulse-3",
                pulse_name="Direct Pulse One",
                pulse_created=now,
                is_aggregator=False,
                source="direct-feed-one",
                source_url="https://direct.example/one",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="url",
                indicator_value="https://direct-two.example/path",
                pulse_id="pulse-4",
                pulse_name="Direct Pulse Two",
                pulse_created=now,
                is_aggregator=False,
                source="direct-feed-two",
                source_url="https://direct.example/two",
                fetched_at=now,
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

    client = TestClient(app)
    response = client.get("/api/threat-intel/source-split?days=30")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_refs"] == 4, body
    assert body["aggregator_count"] == 2, body
    assert body["direct_count"] == 2, body
    assert len(body["by_indicator_type"]) >= 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)