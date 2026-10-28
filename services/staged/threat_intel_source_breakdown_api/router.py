from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_source_breakdown

router = APIRouter(prefix="/api")


class ThreatIntelSourceBreakdown(BaseModel):
    source: Optional[str]
    indicators: int
    pulses: int
    aggregator_rows: int
    newest_pulse_created: Optional[datetime]


class ThreatIntelIndicatorTypeCount(BaseModel):
    indicator_type: Optional[str]
    count: int


class ThreatIntelSourceBreakdownTotals(BaseModel):
    rows: int
    sources: int


class ThreatIntelSourceBreakdownResponse(BaseModel):
    sources: List[ThreatIntelSourceBreakdown]
    by_indicator_type: List[ThreatIntelIndicatorTypeCount]
    totals: ThreatIntelSourceBreakdownTotals


@router.get(
    "/threat-intel/sources",
    response_model=ThreatIntelSourceBreakdownResponse,
)
def get_threat_intel_sources(
    session: Session = Depends(get_session),
) -> ThreatIntelSourceBreakdownResponse:
    return get_source_breakdown(session)


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now,
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.com",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now,
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="ipv4",
                    indicator_value="198.51.100.1",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=now,
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/sources")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["rows"] == 3
        assert payload["totals"]["sources"] == 2

        alpha = next(item for item in payload["sources"] if item["source"] == "alpha")
        assert alpha["indicators"] == 2
        assert alpha["pulses"] >= 1
        assert alpha["aggregator_rows"] >= 1

        assert "ipv4" in {
            item["indicator_type"] for item in payload["by_indicator_type"]
        }

    print("PASS")