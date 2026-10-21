from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, VulnLink


def _pulse_query():
    return (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            func.max(ThreatIntelRef.pulse_name).label("pulse_name"),
            func.max(ThreatIntelRef.pulse_created).label("pulse_created"),
            func.count(distinct(ThreatIntelRef.id)).label("indicator_count"),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ).label("aggregator_count"),
            func.count(distinct(VulnLink.server_id)).label("linked_server_count"),
            func.count(distinct(ThreatIntelRef.source)).label("source_count"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        )
        .outerjoin(
            VulnLink,
            VulnLink.match_value == ThreatIntelRef.indicator_value,
        )
        .group_by(ThreatIntelRef.pulse_id)
        .order_by(ThreatIntelRef.pulse_id)
    )


def get_pulses(session: Session = Depends(get_session)) -> dict:
    """Return pulse coverage and aggregate indicator/link counts."""
    rows = session.execute(_pulse_query()).mappings().all()
    pulses = [dict(row) for row in rows]
    return {
        "pulses": pulses,
        "total_pulses": len(pulses),
    }


def get_pulse_indicators(
    pulse_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Return the indicators belonging to one pulse and their server counts."""
    query = (
        select(
            ThreatIntelRef.id.label("id"),
            ThreatIntelRef.indicator_type.label("indicator_type"),
            ThreatIntelRef.indicator_value.label("indicator_value"),
            ThreatIntelRef.is_aggregator.label("is_aggregator"),
            ThreatIntelRef.source.label("source"),
            ThreatIntelRef.source_url.label("source_url"),
            ThreatIntelRef.fetched_at.label("fetched_at"),
            func.count(distinct(VulnLink.server_id)).label("linked_server_count"),
        )
        .outerjoin(
            VulnLink,
            VulnLink.match_value == ThreatIntelRef.indicator_value,
        )
        .where(ThreatIntelRef.pulse_id == pulse_id)
        .group_by(
            ThreatIntelRef.id,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            ThreatIntelRef.source_url,
            ThreatIntelRef.fetched_at,
        )
        .order_by(ThreatIntelRef.id)
    )
    rows = session.execute(query).mappings().all()
    return {
        "pulse_id": pulse_id,
        "indicators": [dict(row) for row in rows],
    }


# Compatibility aliases for callers using the names from the service exemplar.
get_pulse_coverage = get_pulses
get_threat_intel_pulses = get_pulses
list_pulses = get_pulses
list_pulse_indicators = get_pulse_indicators
get_indicators_for_pulse = get_pulse_indicators


if __name__ == "__main__":
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
    VulnLink.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="cve",
                    indicator_value="CVE-2024-0001",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse One",
                    pulse_created=now,
                    is_aggregator=True,
                    source="example",
                    source_url="https://example.test/one",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="cve",
                    indicator_value="CVE-2024-0002",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse One",
                    pulse_created=now,
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/two",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="cve",
                    indicator_value="CVE-2024-0003",
                    pulse_id="pulse-2",
                    pulse_name="Example Pulse Two",
                    pulse_created=now,
                    is_aggregator=True,
                    source="other",
                    source_url="https://example.test/three",
                    fetched_at=now,
                ),
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=101,
                    match_basis="indicator",
                    match_value="CVE-2024-0001",
                    match_confidence=0.99,
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=102,
                    match_basis="indicator",
                    match_value="CVE-2024-0002",
                    match_confidence=0.98,
                    linked_at=now,
                ),
            ]
        )
        db.commit()

    that_app = FastAPI()

    def override_session() -> Generator[Session, None, None]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/threat-intel/pulses")
    def pulses(session: Session = Depends(get_session)):
        return get_pulses(session=session)

    @that_app.get("/api/threat-intel/pulses/{pulse_id}/indicators")
    def indicators(pulse_id: str, session: Session = Depends(get_session)):
        return get_pulse_indicators(pulse_id=pulse_id, session=session)

    client = TestClient(that_app)
    response = client.get("/api/threat-intel/pulses")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_pulses"] == 2
    pulse = next(item for item in payload["pulses"] if item["pulse_id"] == "pulse-1")
    assert pulse["indicator_count"] == 2
    assert pulse["linked_server_count"] == 2

    response = client.get("/api/threat-intel/pulses/pulse-1/indicators")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["indicators"]) == 2
    assert sorted(item["linked_server_count"] for item in payload["indicators"]) == [1, 1]

    print("PASS")