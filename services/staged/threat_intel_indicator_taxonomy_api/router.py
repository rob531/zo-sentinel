from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef
from .logic import get_indicator_taxonomy

router = APIRouter(prefix="/api")


@router.get("/threat-intel/indicator-taxonomy")
def threat_intel_indicator_taxonomy(
    session: Session = Depends(get_session),
):
    return get_indicator_taxonomy(session)


def _run_self_test() -> None:
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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(engine)

    with TestingSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id=101,
                    pulse_name="Pulse One",
                    pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/1",
                    fetched_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id=101,
                    pulse_name="Pulse One",
                    pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example/2",
                    fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id=202,
                    pulse_name="Pulse Two",
                    pulse_created=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/1",
                    fetched_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="sample.test",
                    pulse_id=202,
                    pulse_name="Pulse Two",
                    pulse_created=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="gamma",
                    source_url="https://gamma.example/1",
                    fetched_at=datetime(2025, 1, 5, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/indicator-taxonomy")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["total_refs"] == 4
        assert len(payload["indicator_types"]) == 2
        assert sum(item["refs"] for item in payload["indicator_types"]) == 4
        assert payload["summary"]["aggregator_ref_count"] == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()