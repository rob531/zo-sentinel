"""threat_intel_refs_api logic layer."""

from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, VulnLink


def get_threat_intel_for_server(
    server_id: int,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Fetch threat intel references for a server via VulnLink join."""
    stmt = (
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.source,
            ThreatIntelRef.source_url,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
        )
        .join(VulnLink, VulnLink.advisory_id == ThreatIntelRef.pulse_id)
        .where(VulnLink.server_id == server_id)
        .order_by(ThreatIntelRef.pulse_created.desc())
    )
    rows = session.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


if __name__ == "__main__":
    import json
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from app.db import get_session
    from app.models import Base, ThreatIntelRef, VulnLink

    from sqlalchemy import StaticPool, create_engine

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)

    def make_session():
        return Session(bind=engine)

    def seed_data():
        sess = make_session()
        vl1 = VulnLink(
            id=1, server_id=100, advisory_id=1, match_value="cve-2021-1",
            match_basis="indicator", match_confidence=0.95, linked_at=datetime.now()
        )
        vl2 = VulnLink(
            id=2, server_id=100, advisory_id=2, match_value="cve-2022-1",
            match_basis="indicator", match_confidence=0.90, linked_at=datetime.now()
        )
        vl3 = VulnLink(
            id=3, server_id=100, advisory_id=3, match_value="cve-2023-1",
            match_basis="indicator", match_confidence=0.85, linked_at=datetime.now()
        )
        vl4 = VulnLink(
            id=4, server_id=200, advisory_id=1, match_value="cve-2021-1",
            match_basis="indicator", match_confidence=0.95, linked_at=datetime.now()
        )
        sess.add_all([vl1, vl2, vl3, vl4])

        tir1 = ThreatIntelRef(
            id=1, pulse_id=1, indicator_type="ipv4", indicator_value="1.2.3.4",
            pulse_name="Pulse Alpha", source="SourceA", source_url="http://a.com",
            pulse_created=datetime(2023, 1, 15), is_aggregator=True, fetched_at=datetime.now()
        )
        tir2 = ThreatIntelRef(
            id=2, pulse_id=2, indicator_type="domain", indicator_value="evil.com",
            pulse_name="Pulse Beta", source="SourceB", source_url="http://b.com",
            pulse_created=datetime(2023, 2, 20), is_aggregator=False, fetched_at=datetime.now()
        )
        tir3 = ThreatIntelRef(
            id=3, pulse_id=3, indicator_type="hash", indicator_value="abc123",
            pulse_name="Pulse Gamma", source="SourceC", source_url="http://c.com",
            pulse_created=datetime(2023, 3, 25), is_aggregator=True, fetched_at=datetime.now()
        )
        tir4 = ThreatIntelRef(
            id=4, pulse_id=1, indicator_type="ipv4", indicator_value="5.6.7.8",
            pulse_name="Pulse Alpha 2", source="SourceA", source_url="http://a2.com",
            pulse_created=datetime(2023, 1, 10), is_aggregator=False, fetched_at=datetime.now()
        )
        sess.add_all([tir1, tir2, tir3, tir4])
        sess.commit()

    seed_data()

    app = FastAPI()
    app.dependency_overrides[get_session] = make_session

    from fastapi.testclient import TestClient

    client = TestClient(app)

    response = client.get("/api/servers/100/threat-intel")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert isinstance(data, list), "Response should be a list"
    assert len(data) >= 3, f"Expected at least 3 items, got {len(data)}"

    required_fields = ["indicator_type", "indicator_value", "pulse_name",
                       "source", "source_url", "pulse_created", "is_aggregator"]
    first_row = data[0]
    for field in required_fields:
        assert field in first_row, f"Missing required field: {field}"

    print("PASS")