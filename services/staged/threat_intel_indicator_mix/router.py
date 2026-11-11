from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_indicator_mix as build_indicator_mix

router = APIRouter(prefix="/api")


class IndicatorTypeSummary(BaseModel):
    indicator_type: str
    count: int
    pulses: int
    last_fetched: Optional[datetime]
    newest_pulse_created: Optional[datetime]


class SourceSummary(BaseModel):
    source: str
    count: int
    last_fetched: Optional[datetime]


class IndicatorMixTotals(BaseModel):
    indicators: int
    indicator_types: int
    sources: int
    aggregator_rows: int


class IndicatorMixResponse(BaseModel):
    indicator_types: List[IndicatorTypeSummary]
    sources: List[SourceSummary]
    totals: IndicatorMixTotals


@router.get(
    "/threat-intel/indicator-mix",
    response_model=IndicatorMixResponse,
    status_code=200,
)
def read_indicator_mix(
    session: Session = Depends(get_session),
) -> IndicatorMixResponse:
    return build_indicator_mix(session)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type VARCHAR NOT NULL,
                    indicator_value VARCHAR NOT NULL,
                    pulse_id VARCHAR NOT NULL,
                    pulse_name VARCHAR,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN NOT NULL DEFAULT 0,
                    source VARCHAR NOT NULL,
                    source_url VARCHAR NOT NULL,
                    fetched_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs (
                    id,
                    indicator_type,
                    indicator_value,
                    pulse_id,
                    pulse_name,
                    pulse_created,
                    is_aggregator,
                    source,
                    source_url,
                    fetched_at
                ) VALUES
                    (
                        1,
                        'ipv4',
                        '192.0.2.1',
                        'pulse-1',
                        'Pulse One',
                        '2026-01-01T00:00:00Z',
                        0,
                        'alpha',
                        'https://example.test/alpha',
                        '2026-01-02T00:00:00Z'
                    ),
                    (
                        2,
                        'domain',
                        'example.test',
                        'pulse-2',
                        'Pulse Two',
                        '2026-01-03T00:00:00Z',
                        1,
                        'beta',
                        'https://example.test/beta',
                        '2026-01-04T00:00:00Z'
                    ),
                    (
                        3,
                        'domain',
                        'second.test',
                        'pulse-2',
                        'Pulse Two',
                        '2026-01-03T00:00:00Z',
                        0,
                        'alpha',
                        'https://example.test/alpha',
                        '2026-01-05T00:00:00Z'
                    )
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/indicator-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["indicators"] == 3
        assert payload["totals"]["indicator_types"] == 2
        assert payload["totals"]["sources"] == 2
        assert payload["totals"]["aggregator_rows"] == 1
        for item in payload["indicator_types"]:
            assert item["last_fetched"] is None or isinstance(item["last_fetched"], str)
            assert item["newest_pulse_created"] is None or isinstance(
                item["newest_pulse_created"], str
            )
        for item in payload["sources"]:
            assert item["last_fetched"] is None or isinstance(item["last_fetched"], str)

    print("PASS")