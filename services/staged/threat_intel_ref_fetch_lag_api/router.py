from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef
from .logic import get_fetch_lag

router = APIRouter(prefix="/api", tags=["threat-intel"])


class SourceLag(BaseModel):
    source: str
    total: int
    stale_count: int
    max_age_hours: float
    oldest_fetched_at: datetime


class IndicatorTypeLag(BaseModel):
    indicator_type: str
    count: int
    max_age_hours: float


class FetchLagResponse(BaseModel):
    hours: int
    generated_at: datetime
    by_source: List[SourceLag]
    by_indicator_type: List[IndicatorTypeLag]
    overall_max_age_hours: float
    total: int


@router.get(
    "/threat-intel/fetch-lag",
    response_model=FetchLagResponse,
    summary="Report external threat-intelligence reference fetch lag",
)
def fetch_lag(
    hours: int = Query(default=168, ge=0),
    source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> FetchLagResponse:
    payload: Dict[str, Any] = get_fetch_lag(
        session=session,
        hours=hours,
        source=source,
    )
    return FetchLagResponse(**payload)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value=f"198.51.100.{index}",
            pulse_id=f"pulse-abuse-{index}",
            pulse_name=f"abuse pulse {index}",
            pulse_created=now - timedelta(hours=2),
            is_aggregator=False,
            source="abuse.ch",
            source_url=f"https://abuse.ch/example/{index}",
            fetched_at=now - timedelta(hours=2),
        )
        for index in range(3)
    ]
    rows.extend(
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value=f"203.0.113.{index}",
            pulse_id=f"pulse-circl-{index}",
            pulse_name=f"circl pulse {index}",
            pulse_created=now - timedelta(hours=400),
            is_aggregator=False,
            source="circl.lu",
            source_url=f"https://circl.lu/example/{index}",
            fetched_at=now - timedelta(hours=400),
        )
        for index in range(2)
    )
    rows.append(
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id="pulse-domain",
            pulse_name="domain pulse",
            pulse_created=now - timedelta(hours=10),
            is_aggregator=False,
            source="abuse.ch",
            source_url="https://abuse.ch/example/domain",
            fetched_at=now - timedelta(hours=10),
        )
    )

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session()

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/fetch-lag")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 6
        circl = next(item for item in payload["by_source"] if item["source"] == "circl.lu")
        assert circl["stale_count"] == 2
        assert circl["max_age_hours"] >= 400
        domain = next(
            item
            for item in payload["by_indicator_type"]
            if item["indicator_type"] == "domain"
        )
        assert domain["count"] == 1

    print("PASS")


if __name__ == "__main__":
    from fastapi import FastAPI

    _self_test()