"""
Threat Intel Lookup API

Provides threat intelligence lookups by indicator type and value.
Queries threat_intel_refs table for matching indicators.
"""
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api/threat")


class PulseMatch(BaseModel):
    pulse_id: str
    pulse_name: str
    pulse_created: str
    is_aggregator: bool
    source: str
    source_url: Optional[str] = None


class LookupResponse(BaseModel):
    indicator_type: str
    indicator_value: str
    matches: list[PulseMatch]


@router.get("/lookup", response_model=LookupResponse)
def lookup_threat(
    type: str = Query(..., description="Indicator type (e.g., ip, domain, hash)"),
    value: str = Query(..., description="Indicator value to look up"),
    session: Session = Depends(get_session),
) -> LookupResponse:
    """
    Look up threat intelligence by indicator type and value.
    """
    stmt = select(
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.pulse_name,
        ThreatIntelRef.pulse_created,
        ThreatIntelRef.is_aggregator,
        ThreatIntelRef.source,
        ThreatIntelRef.source_url,
    ).where(
        ThreatIntelRef.indicator_type == type,
        ThreatIntelRef.indicator_value == value,
    )

    results = session.execute(stmt).fetchall()

    matches = [
        PulseMatch(
            pulse_id=row.pulse_id,
            pulse_name=row.pulse_name,
            pulse_created=row.pulse_created.isoformat() if row.pulse_created else "",
            is_aggregator=row.is_aggregator,
            source=row.source,
            source_url=row.source_url,
        )
        for row in results
    ]

    return LookupResponse(
        indicator_type=type,
        indicator_value=value,
        matches=matches,
    )


if __name__ == "__main__":
    import sys

    try:
        from fastapi.testclient import TestClient
        from app.db import Base, engine

        # Import app for dependency override
        from main import app

        # Create tables in test database
        Base.metadata.create_all(bind=engine)

        # Seed test data
        session = next(get_session())
        try:
            # Clear existing test data
            session.query(ThreatIntelRef).filter(
                ThreatIntelRef.indicator_value.like("test_%")
            ).delete(synchronize_session=False)
            session.commit()

            # Insert 3 threat refs with different indicators
            test_refs = [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="test_192.168.1.100",
                    pulse_id="pulse_001",
                    pulse_name="Malicious IP Test",
                    pulse_created=datetime.utcnow(),
                    is_aggregator=False,
                    source="test_source",
                    source_url="https://example.com/pulse/001",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="test_malware.example.com",
                    pulse_id="pulse_002",
                    pulse_name="Malware Domain Test",
                    pulse_created=datetime.utcnow(),
                    is_aggregator=True,
                    source="test_source",
                    source_url="https://example.com/pulse/002",
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="test_abc123def456",
                    pulse_id="pulse_003",
                    pulse_name="Ransomware Hash Test",
                    pulse_created=datetime.utcnow(),
                    is_aggregator=False,
                    source="test_source",
                    source_url="https://example.com/pulse/003",
                ),
            ]

            for ref in test_refs:
                session.add(ref)
            session.commit()

            # Run contract test using FastAPI TestClient
            with TestClient(app) as client:
                # Query the IP indicator - should match exactly 1
                response = client.get(
                    "/api/threat/lookup",
                    params={"type": "ip", "value": "test_192.168.1.100"},
                )

                assert response.status_code == 200, f"Expected 200, got {response.status_code}"

                data = response.json()
                assert data["indicator_type"] == "ip"
                assert data["indicator_value"] == "test_192.168.1.100"
                assert len(data["matches"]) == 1, f"Expected 1 match, got {len(data['matches'])}"

                match = data["matches"][0]
                assert match["pulse_id"] == "pulse_001"
                assert match["pulse_name"] == "Malicious IP Test"
                assert match["is_aggregator"] is False
                assert match["source"] == "test_source"

            print("PASS")
            sys.exit(0)

        finally:
            session.close()

    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)