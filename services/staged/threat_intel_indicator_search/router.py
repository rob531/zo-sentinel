"""HTTP surface for threat intelligence indicator search."""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_threat_intel_indicators

router = APIRouter(prefix="/api", tags=["threat-intel"])


class IndicatorSearchParams(BaseModel):
    q: str | None = None
    indicator_type: str | None = None
    pulse_name: str | None = None
    exclude_aggregators: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ThreatIntelIndicator(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: int | str
    pulse_name: str | None = None
    pulse_created: datetime | None = None
    is_aggregator: bool
    source: str
    source_url: str
    fetched_at: datetime


class ThreatIntelIndicatorSearchResponse(BaseModel):
    total: int
    items: list[ThreatIntelIndicator]
    limit: int
    offset: int


@router.get(
    "/threat-indicators",
    response_model=ThreatIntelIndicatorSearchResponse,
)
@router.get(
    "/threat-inticators",
    response_model=ThreatIntelIndicatorSearchResponse,
    include_in_schema=False,
)
def search_threat_intel_indicators(
    q: str | None = Query(default=None),
    indicator_type: str | None = Query(default=None),
    pulse_name: str | None = Query(default=None),
    exclude_aggregators: bool = Query(default=False),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    db: Session = Depends(get_session),
) -> ThreatIntelIndicatorSearchResponse:
    params = IndicatorSearchParams(
        q=q,
        indicator_type=indicator_type,
        pulse_name=pulse_name,
        exclude_aggregators=exclude_aggregators,
        limit=limit,
        offset=offset,
    )
    result = get_threat_intel_indicators(
        db,
        q=params.q,
        indicator_type=params.indicator_type,
        pulse_name=params.pulse_name,
        exclude_aggregators=params.exclude_aggregators,
        limit=params.limit,
        offset=params.offset,
    )

    if isinstance(result, dict):
        payload = result
    elif isinstance(result, tuple) and len(result) == 2:
        first, second = result
        if isinstance(first, list):
            items = first
            total = int(second)
        else:
            total = int(first)
            items = list(second)
        payload = {
            "total": total,
            "items": items,
            "limit": params.limit,
            "offset": params.offset,
        }
    else:
        items = list(result)
        payload = {
            "total": len(items),
            "items": items,
            "limit": params.limit,
            "offset": params.offset,
        }

    normalized_items = [
        item
        if isinstance(item, dict)
        else ThreatIntelIndicator.model_validate(item, from_attributes=True).model_dump()
        for item in payload.get("items", [])
    ]
    payload["items"] = normalized_items
    payload.setdefault("total", len(normalized_items))
    payload.setdefault("limit", params.limit)
    payload.setdefault("offset", params.offset)
    return ThreatIntelIndicatorSearchResponse.model_validate(payload)


def _self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    with testing_session() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Test Pulse",
                    pulse_created=datetime.now(timezone.utc),
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/1",
                    fetched_at=datetime.now(timezone.utc),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ip",
                    indicator_value="198.51.100.2",
                    pulse_id="pulse-2",
                    pulse_name="Test Pulse",
                    pulse_created=datetime.now(timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/2",
                    fetched_at=datetime.now(timezone.utc),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-3",
                    pulse_name="Domain Pulse",
                    pulse_created=datetime.now(timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/3",
                    fetched_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-indicators")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3

        response = client.get(
            "/api/threat-indicators",
            params={"exclude_aggregators": "true"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert not any(item["is_aggregator"] for item in body["items"])

        response = client.get(
            "/api/threat-indicators",
            params={"indicator_type": "unknown"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0

    print("PASS")


if __name__ == "__main__":
    _self_test()