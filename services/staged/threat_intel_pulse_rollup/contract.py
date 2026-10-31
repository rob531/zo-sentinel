from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef
from services.staged.threat_intel_pulse_rollup.router import router


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    ThreatIntelRef.__table__.create(bind=engine)

    rows = [
        {
            "id": 1,
            "indicator_type": "ipv4",
            "indicator_value": "192.0.2.1",
            "pulse_id": "pulse-aggregator",
            "pulse_name": "Critical Aggregator",
            "pulse_created": datetime(2024, 1, 1, 12, 0, 0),
            "is_aggregator": True,
            "source": "otx",
            "source_url": "https://example.test/pulses/pulse-aggregator",
            "fetched_at": datetime(2024, 1, 2, 8, 0, 0),
        },
        {
            "id": 2,
            "indicator_type": "domain",
            "indicator_value": "malicious.example",
            "pulse_id": "pulse-aggregator",
            "pulse_name": "Critical Aggregator",
            "pulse_created": datetime(2024, 1, 1, 12, 0, 0),
            "is_aggregator": True,
            "source": "otx",
            "source_url": "https://example.test/pulses/pulse-aggregator",
            "fetched_at": datetime(2024, 1, 2, 9, 0, 0),
        },
        {
            "id": 3,
            "indicator_type": "url",
            "indicator_value": "https://malicious.example/payload",
            "pulse_id": "pulse-aggregator",
            "pulse_name": "Critical Aggregator",
            "pulse_created": datetime(2024, 1, 1, 12, 0, 0),
            "is_aggregator": True,
            "source": "otx",
            "source_url": "https://example.test/pulses/pulse-aggregator",
            "fetched_at": datetime(2024, 1, 2, 10, 0, 0),
        },
        {
            "id": 4,
            "indicator_type": "ipv4",
            "indicator_value": "198.51.100.10",
            "pulse_id": "pulse-normal",
            "pulse_name": "Normal Pulse",
            "pulse_created": datetime(2024, 2, 1, 12, 0, 0),
            "is_aggregator": False,
            "source": "misp",
            "source_url": "https://example.test/pulses/pulse-normal",
            "fetched_at": datetime(2024, 2, 2, 8, 0, 0),
        },
    ]

    with engine.begin() as connection:
        connection.execute(ThreatIntelRef.__table__.insert(), rows)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulses")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_indicators"] == 4
    assert payload["distinct_pulses"] == 2
    assert payload["aggregator_pulses"] == 1
    assert payload["pulses"][0]["indicator_count"] == 3
    assert payload["pulses"][0]["is_aggregator"] is True

    print("PASS")


if __name__ == "__main__":
    run_contract()