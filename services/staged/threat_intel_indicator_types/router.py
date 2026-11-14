"""HTTP surface for threat intelligence indicator-type statistics."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_indicator_types_breakdown

router = APIRouter(prefix="/api", tags=["threat-intel"])


class IndicatorTypeBreakdown(BaseModel):
    indicator_type: str
    ref_count: int
    distinct_values: int
    aggregator_refs: int
    latest_fetched_at: datetime
    age_hours: float


class IndicatorSourceBreakdown(BaseModel):
    source: str
    ref_count: int
    latest_fetched_at: datetime


class IndicatorTypesResponse(BaseModel):
    total_refs: int
    types: list[IndicatorTypeBreakdown]
    sources: list[IndicatorSourceBreakdown]


@router.get(
    "/threat-intel/indicator-types",
    response_model=IndicatorTypesResponse,
)
def get_indicator_types(
    db: Session = Depends(get_session),
) -> IndicatorTypesResponse:
    breakdown = get_indicator_types_breakdown(db)
    return IndicatorTypesResponse.model_validate(breakdown)


if __name__ == "__main__":
    from datetime import timezone

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(test_engine)

    now = datetime.now(timezone.utc)
    rows = [
        ("alpha", "value-a", 1, "source-one", True),
        ("alpha", "value-b", 2, "source-one", False),
        ("alpha", "value-c", 3, "source-two", False),
        ("beta", "value-d", 4, "source-two", True),
        ("beta", "value-e", 5, "source-two", False),
    ]

    with TestSession() as session:
        for index, (kind, value, pulse_id, source, aggregator) in enumerate(rows, 1):
            session.add(
                ThreatIntelRef(
                    id=index,
                    indicator_type=kind,
                    indicator_value=value,
                    pulse_id=pulse_id,
                    pulse_name=f"pulse-{pulse_id}",
                    pulse_created=now,
                    is_aggregator=aggregator,
                    source=source,
                    source_url=f"https://example.test/{source}",
                    fetched_at=now,
                )
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/indicator-types")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_refs"] == 5, payload
        assert len(payload["types"]) == 2, payload
        assert any(
            row["indicator_type"] == "alpha" and row["ref_count"] == 3
            for row in payload["types"]
        ), payload

    print("PASS")