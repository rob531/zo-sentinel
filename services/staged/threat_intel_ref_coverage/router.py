"""HTTP surface for threat intelligence reference coverage."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_ref_coverage

router = APIRouter(
    prefix="/api/threat-intel/refs",
    tags=["threat-intel-ref-coverage"],
)


class CoverageByType(BaseModel):
    indicator_type: str
    count: int
    aggregator_count: int
    latest_pulse_created: datetime | None
    latest_fetched_at: datetime | None


class LatestThreatIntelRef(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: int
    pulse_name: str | None
    pulse_created: datetime | None
    is_aggregator: bool
    source: str
    source_url: str
    fetched_at: datetime


class ThreatIntelRefCoverageResponse(BaseModel):
    total_refs: int
    by_type: list[CoverageByType]
    latest: list[LatestThreatIntelRef]


@router.get(
    "/coverage",
    response_model=ThreatIntelRefCoverageResponse,
)
def threat_intel_ref_coverage(
    db: Session = Depends(get_session),
) -> ThreatIntelRefCoverageResponse:
    return ThreatIntelRefCoverageResponse(**get_ref_coverage(db))


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False)()

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id=101,
            pulse_name="Pulse One",
            pulse_created=now,
            is_aggregator=True,
            source="seed",
            source_url="https://example.test/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id=101,
            pulse_name="Pulse One",
            pulse_created=now,
            is_aggregator=False,
            source="seed",
            source_url="https://example.test/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id=102,
            pulse_name="Pulse Two",
            pulse_created=now,
            is_aggregator=False,
            source="seed",
            source_url="https://example.test/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id=103,
            pulse_name="Pulse Three",
            pulse_created=now,
            is_aggregator=False,
            source="seed",
            source_url="https://example.test/4",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="domain",
            indicator_value="example.net",
            pulse_id=104,
            pulse_name="Pulse Four",
            pulse_created=now,
            is_aggregator=False,
            source="seed",
            source_url="https://example.test/5",
            fetched_at=now,
        ),
    ]
    testing_session.add_all(rows)
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with testing_session:
            yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/refs/coverage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_refs"] == 5
    assert len(payload["by_type"]) == 2
    aggregator_type = next(
        item["indicator_type"]
        for item in payload["by_type"]
        if item["aggregator_count"] == 1
    )
    assert aggregator_type == "ip"
    assert len(payload["latest"]) == 5

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()