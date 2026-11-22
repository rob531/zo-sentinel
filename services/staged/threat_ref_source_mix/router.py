"""HTTP surface for threat reference source-mix statistics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Union

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import source_mix

router = APIRouter(prefix="/api", tags=["threat-refs"])


class SourceMixItem(BaseModel):
    source: str
    indicators: int
    pulses: int


class IndicatorTypeItem(BaseModel):
    indicator_type: str
    count: int


class SourceMixResponse(BaseModel):
    max_age_hours: int
    generated_at: Union[str, datetime]
    by_source: list[SourceMixItem]
    by_indicator_type: list[IndicatorTypeItem]
    aggregator_rows: int
    stale_rows: int
    total_rows: int


@router.get("/threat-refs/source-mix", response_model=SourceMixResponse)
def get_source_mix(
    max_age_hours: int = Query(default=168, ge=1),
    db: Session = Depends(get_session),
) -> SourceMixResponse:
    result = source_mix(db, max_age_hours=max_age_hours)
    return SourceMixResponse(**result)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now,
            is_aggregator=False,
            source="alpha",
            source_url="https://alpha.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ipv4",
            indicator_value="192.0.2.2",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now,
            is_aggregator=True,
            source="alpha",
            source_url="https://alpha.example/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now,
            is_aggregator=False,
            source="alpha",
            source_url="https://alpha.example/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.net",
            pulse_id="pulse-3",
            pulse_name="Pulse Three",
            pulse_created=now,
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="old.example",
            pulse_id="pulse-4",
            pulse_name="Pulse Four",
            pulse_created=now - timedelta(days=10),
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/2",
            fetched_at=now - timedelta(days=10),
        ),
    ]

    with Session(engine) as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-refs/source-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 5
        assert len(payload["by_source"]) == 2
        assert sum(item["indicators"] for item in payload["by_source"]) == 5
        assert payload["aggregator_rows"] == 1
        assert payload["stale_rows"] == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()