from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session

from .logic import get_top_indicators as get_top_indicators_data


router = APIRouter(tags=["threat-intel"])


class TopIndicatorItem(BaseModel):
    indicator_value: str
    indicator_type: str
    ref_count: int
    pulse_count: int
    latest_pulse_created: Optional[datetime] = None
    sources: List[str] = Field(default_factory=list)


class TopIndicatorsResponse(BaseModel):
    limit: int
    indicator_type: Optional[str] = None
    items: List[TopIndicatorItem] = Field(default_factory=list)
    total_refs: int


@router.get(
    "/api/threat-intel/top-indicators",
    response_model=TopIndicatorsResponse,
    summary="Get the most frequently referenced threat indicators",
)
async def get_top_indicators(
    limit: int = Query(25, ge=1, le=100),
    indicator_type: Optional[str] = Query(None),
    session: Any = Depends(get_session),
) -> Any:
    result = get_top_indicators_data(session, limit, indicator_type)
    if isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone

    import httpx
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ThreatIntelRef

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(test_engine)

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="203.0.113.10",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=False,
            source="alpha",
            source_url="https://example.invalid/a",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="203.0.113.10",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=now,
            is_aggregator=False,
            source="beta",
            source_url="https://example.invalid/b",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="malicious.example",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=False,
            source="alpha",
            source_url="https://example.invalid/c",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="other.example",
            pulse_id="pulse-c",
            pulse_name="Pulse C",
            pulse_created=now,
            is_aggregator=False,
            source="gamma",
            source_url="https://example.invalid/d",
            fetched_at=now,
        ),
    ]

    with TestSession() as db:
        db.add_all(rows)
        db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    async def run_test() -> None:
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/threat-intel/top-indicators")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"][0]["indicator_value"] == "203.0.113.10"
            assert payload["items"][0]["ref_count"] == 2
        print("PASS")

    try:
        asyncio.run(run_test())
    finally:
        test_engine.dispose()