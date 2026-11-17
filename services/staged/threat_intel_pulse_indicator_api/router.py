from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import ThreatIntelRef
from .logic import get_threat_intel_pulse_indicators

router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get("/threat-intel/pulses/indicators")
def threat_intel_pulse_indicators(session=Depends(get_session)):
    return get_threat_intel_pulse_indicators(session)


def _self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse 1",
                    pulse_created=now,
                    is_aggregator=True,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse 1",
                    pulse_created=now,
                    is_aggregator=True,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://example.test/malware",
                    pulse_id="pulse-2",
                    pulse_name="Example Pulse 2",
                    pulse_created=now,
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/pulse-2",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/threat-intel/pulses/indicators")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_indicators"] == 3
        assert payload["total_pulses"] == 2
        pulse = next(
            item for item in payload["pulses"] if item["pulse_id"] == "pulse-1"
        )
        assert pulse["indicator_count"] == 2
        assert pulse["by_type"]
        assert sum(pulse["by_type"].values()) == 2

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")