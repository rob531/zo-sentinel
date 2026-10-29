from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import ThreatIntelRefreshLagResponse, get_threat_intel_refresh_lag

router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threat-intel/refresh-lag",
    response_model=ThreatIntelRefreshLagResponse,
)
def threat_intel_refresh_lag(
    session: Session = Depends(get_session),
) -> ThreatIntelRefreshLagResponse:
    return get_threat_intel_refresh_lag(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as seed_session:
        seed_session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Fresh pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="otx",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now - timedelta(minutes=5),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-2",
                    pulse_name="Recent pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="otx",
                    source_url="https://example.test/pulse-2",
                    fetched_at=now - timedelta(hours=2),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="ip",
                    indicator_value="198.51.100.1",
                    pulse_id="pulse-3",
                    pulse_name="Stale pulse",
                    pulse_created=now - timedelta(days=30),
                    is_aggregator=False,
                    source="otx",
                    source_url="https://example.test/pulse-3",
                    fetched_at=now - timedelta(days=30),
                ),
            ]
        )
        seed_session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/refresh-lag")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_refs"] == 3, payload
        assert payload["stale_over_7d"] == 1, payload
        assert sum(payload["by_age_band"].values()) == 3, payload

    engine.dispose()
    print("PASS")