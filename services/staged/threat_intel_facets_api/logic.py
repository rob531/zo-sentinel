from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api", tags=["threat-intel"])

PulseId = Optional[Union[str, int]]


class IndicatorTypeFacet(BaseModel):
    indicator_type: Optional[str] = None
    indicator_count: int
    pulse_count: int


class PulseFacet(BaseModel):
    pulse_id: PulseId = None
    pulse_name: Optional[str] = None
    indicator_count: int


class FreshnessFacet(BaseModel):
    fresh_24h: int
    stale: int


class ThreatIntelFacetsResponse(BaseModel):
    indicator_types: list[IndicatorTypeFacet] = Field(default_factory=list)
    top_pulses: list[PulseFacet] = Field(default_factory=list)
    freshness: FreshnessFacet
    generated_at: datetime


def get_threat_intel_facets(session) -> ThreatIntelFacetsResponse:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=24)

    indicator_rows = session.execute(
        text(
            """
            SELECT
                indicator_type,
                COUNT(DISTINCT indicator_value) AS indicator_count,
                COUNT(DISTINCT pulse_id) AS pulse_count
            FROM threat_intel_refs
            GROUP BY indicator_type
            ORDER BY indicator_type
            """
        )
    ).mappings()

    pulse_rows = session.execute(
        text(
            """
            SELECT
                pulse_id,
                pulse_name,
                COUNT(DISTINCT indicator_value) AS indicator_count
            FROM threat_intel_refs
            WHERE pulse_id IS NOT NULL
            GROUP BY pulse_id, pulse_name
            ORDER BY indicator_count DESC, pulse_id
            LIMIT :limit
            """
        ),
        {"limit": 20},
    ).mappings()

    freshness_row = session.execute(
        text(
            """
            SELECT
                COALESCE(
                    SUM(CASE WHEN fetched_at >= :cutoff THEN 1 ELSE 0 END),
                    0
                ) AS fresh_24h,
                COUNT(*) - COALESCE(
                    SUM(CASE WHEN fetched_at >= :cutoff THEN 1 ELSE 0 END),
                    0
                ) AS stale
            FROM threat_intel_refs
            """
        ),
        {"cutoff": cutoff},
    ).mappings().one()

    return ThreatIntelFacetsResponse(
        indicator_types=[
            IndicatorTypeFacet(
                indicator_type=row["indicator_type"],
                indicator_count=int(row["indicator_count"]),
                pulse_count=int(row["pulse_count"]),
            )
            for row in indicator_rows
        ],
        top_pulses=[
            PulseFacet(
                pulse_id=row["pulse_id"],
                pulse_name=row["pulse_name"],
                indicator_count=int(row["indicator_count"]),
            )
            for row in pulse_rows
        ],
        freshness=FreshnessFacet(
            fresh_24h=int(freshness_row["fresh_24h"]),
            stale=int(freshness_row["stale"]),
        ),
        generated_at=generated_at,
    )


@router.get("/threat-intel/facets", response_model=ThreatIntelFacetsResponse)
def facets(session=Depends(get_session)) -> ThreatIntelFacetsResponse:
    return get_threat_intel_facets(session)


if __name__ == "__main__":
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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=now,
                    is_aggregator=0,
                    source="test",
                    source_url="https://example.test/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ip",
                    indicator_value="198.51.100.2",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=now,
                    is_aggregator=0,
                    source="test",
                    source_url="https://example.test/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    pulse_created=now,
                    is_aggregator=1,
                    source="test",
                    source_url="https://example.test/3",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/threat-intel/facets")
    response.raise_for_status()
    payload = response.json()

    assert len(payload["indicator_types"]) == 2
    ip_facet = next(
        item for item in payload["indicator_types"] if item["indicator_type"] == "ip"
    )
    assert ip_facet["indicator_count"] == 2
    assert payload["top_pulses"][0]["pulse_id"] == "pulse-a"
    assert payload["top_pulses"][0]["indicator_count"] == 2
    print("PASS")