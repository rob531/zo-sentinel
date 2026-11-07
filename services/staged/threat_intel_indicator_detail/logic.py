"""Data access and computation for threat intelligence indicator details."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def get_indicator_detail(db: Session, indicator_id: int) -> ThreatIntelRef | None:
    """Return a threat intelligence reference by primary key."""
    return db.execute(
        select(ThreatIntelRef).where(ThreatIntelRef.id == indicator_id)
    ).scalar_one_or_none()


def get_threat_intel_indicator_detail(
    db: Session, indicator_id: int
) -> ThreatIntelRef | None:
    """Return a threat intelligence reference by primary key."""
    return get_indicator_detail(db, indicator_id)


def get_threat_intel_ref(db: Session, indicator_id: int) -> ThreatIntelRef | None:
    """Return a threat intelligence reference by primary key."""
    return get_indicator_detail(db, indicator_id)


def _self_test() -> None:
    from datetime import datetime

    from fastapi import Depends, FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        session.add(
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.10",
                pulse_id="pulse-1",
                pulse_name="Test Pulse",
                pulse_created=datetime(2024, 1, 1, 12, 0, 0),
                is_aggregator=True,
                source="test-source",
                source_url="https://example.test/indicator/1",
                fetched_at=datetime(2024, 1, 2, 12, 0, 0),
            )
        )
        session.add(
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-2",
                source="test-source",
                source_url="https://example.test/indicator/2",
            )
        )
        session.commit()
        seeded_id = session.query(ThreatIntelRef.id).order_by(ThreatIntelRef.id).first()[0]

    local_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/threat-intel/indicators/{indicator_id}")
    def indicator_detail(indicator_id: int, db: Session = Depends(get_session)):
        row = get_indicator_detail(db, indicator_id)
        if row is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return {
            "id": row.id,
            "indicator_type": row.indicator_type,
            "indicator_value": row.indicator_value,
            "pulse_id": row.pulse_id,
            "pulse_name": row.pulse_name,
            "pulse_created": row.pulse_created,
            "is_aggregator": row.is_aggregator,
            "source": row.source,
            "source_url": row.source_url,
            "fetched_at": row.fetched_at,
        }

    with TestClient(local_app) as client:
        response = client.get(f"/api/threat-intel/indicators/{seeded_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["indicator_value"] == "198.51.100.10"
        assert set(payload) == {
            "id",
            "indicator_type",
            "indicator_value",
            "pulse_id",
            "pulse_name",
            "pulse_created",
            "is_aggregator",
            "source",
            "source_url",
            "fetched_at",
        }

        response = client.get("/api/threat-intel/indicators/999999999")
        assert response.status_code == 404
        assert response.json() == {"error": "not_found"}

    print("PASS")


if __name__ == "__main__":
    _self_test()