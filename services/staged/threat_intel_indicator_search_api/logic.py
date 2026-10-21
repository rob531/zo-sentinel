from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def search_indicators(
    session: Session = Depends(get_session),
    q: str = "",
    type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search threat-intelligence indicators using bound query parameters."""
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 50

    capped_limit = max(0, min(requested_limit, 200))

    statement = select(
        ThreatIntelRef.id,
        ThreatIntelRef.indicator_type,
        ThreatIntelRef.indicator_value,
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.pulse_name,
        ThreatIntelRef.pulse_created,
        ThreatIntelRef.is_aggregator,
        ThreatIntelRef.source,
        ThreatIntelRef.source_url,
        ThreatIntelRef.fetched_at,
    )

    if q:
        statement = statement.where(
            func.lower(ThreatIntelRef.indicator_value).contains(
                q.lower(),
                autoescape=True,
            )
        )

    if type is not None:
        statement = statement.where(ThreatIntelRef.indicator_type == type)

    statement = statement.order_by(
        ThreatIntelRef.pulse_created.desc().nullslast(),
        ThreatIntelRef.id.asc(),
    ).limit(capped_limit)

    rows = session.execute(statement).mappings().all()
    return [dict(row) for row in rows]


search_threat_intel_indicators = search_indicators
get_threat_intel_indicators = search_indicators
query_indicators = search_indicators


__all__ = [
    "search_indicators",
    "search_threat_intel_indicators",
    "get_threat_intel_indicators",
    "query_indicators",
]


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    ThreatIntelRef.__table__.create(bind=test_engine)

    now = datetime.now(timezone.utc)
    seed_rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="203.0.113.10",
            pulse_id=101,
            pulse_name="Malicious IPs",
            pulse_created=now,
            is_aggregator=False,
            source="internal",
            source_url="https://example.test/pulse/101",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="malicious.example.test",
            pulse_id=102,
            pulse_name="Malicious Domains",
            pulse_created=now,
            is_aggregator=True,
            source="internal",
            source_url="https://example.test/pulse/102",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="198.51.100.42",
            pulse_id=103,
            pulse_name="Older IPs",
            pulse_created=None,
            is_aggregator=False,
            source="internal",
            source_url="https://example.test/pulse/103",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="url",
            indicator_value="https://malicious.example.test/path",
            pulse_id=104,
            pulse_name="Malicious URLs",
            pulse_created=now,
            is_aggregator=False,
            source="internal",
            source_url="https://example.test/pulse/104",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="domain",
            indicator_value="safe.example.test",
            pulse_id=105,
            pulse_name="Safe Domains",
            pulse_created=now,
            is_aggregator=False,
            source="internal",
            source_url="https://example.test/pulse/105",
            fetched_at=now,
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/indicators")
    def indicators(
        q: str = "",
        type: str | None = None,
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        results = search_indicators(session=session, q=q, type=type, limit=limit)
        return {
            "query": q,
            "type": type,
            "count": len(results),
            "results": results,
        }

    client = TestClient(that_app)

    response = client.get("/api/threat-intel/indicators?q=")
    assert response.status_code == 200
    assert response.json()["count"] > 0

    response = client.get("/api/threat-intel/indicators?q=MALICIOUS")
    narrowed = response.json()
    assert response.status_code == 200
    assert narrowed["count"] == 2
    assert all("malicious" in row["indicator_value"].lower() for row in narrowed["results"])

    response = client.get("/api/threat-intel/indicators?type=ip")
    filtered = response.json()
    assert response.status_code == 200
    assert filtered["count"] == 2
    assert all(row["indicator_type"] == "ip" for row in filtered["results"])

    response = client.get("/api/threat-intel/indicators?limit=1")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1

    print("PASS")