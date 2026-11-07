"""HTTP surface for threat intelligence unmatched indicators.

Exposes the staged service router and delegates data access to the real
``logic.py`` implementation using the application's SQLAlchemy session.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unmatched_indicators


router = APIRouter(
    prefix="/api/threat-intel",
    tags=["threat-intel"],
)


class IndicatorTypeCounts(BaseModel):
    indicator_type: str
    total: int
    unmatched: int


class RecentUnmatchedIndicator(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str
    fetched_at: datetime


class UnmatchedIndicatorsResponse(BaseModel):
    total_indicators: int
    by_type: list[IndicatorTypeCounts]
    recent_unmatched: list[RecentUnmatchedIndicator]
    generated_at: datetime


@router.get(
    "/unmatched-indicators",
    response_model=UnmatchedIndicatorsResponse,
)
def get_unmatched_indicators_response(
    limit: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_session),
) -> UnmatchedIndicatorsResponse:
    result = get_unmatched_indicators(db, limit=limit)
    return UnmatchedIndicatorsResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry, ThreatIntelRef

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(test_engine)
    SessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    now = datetime.utcnow()
    with SessionLocal() as session:
        session.add(
            ThreatIntelRef(
                id=1,
                indicator_type="domain",
                indicator_value="matched-indicator",
                pulse_id="pulse-1",
                pulse_name="Example pulse",
                pulse_created=now,
                is_aggregator=False,
                source="example-source",
                source_url="https://example.test/matched",
                fetched_at=now,
            )
        )
        session.add(
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="unmatched-one",
                pulse_id="pulse-1",
                pulse_name="Example pulse",
                pulse_created=now,
                is_aggregator=False,
                source="example-source",
                source_url="https://example.test/one",
                fetched_at=now,
            )
        )
        session.add(
            ThreatIntelRef(
                id=3,
                indicator_type="ip",
                indicator_value="unmatched-two",
                pulse_id="pulse-2",
                pulse_name="Second pulse",
                pulse_created=now,
                is_aggregator=False,
                source="example-source",
                source_url="https://example.test/two",
                fetched_at=now,
            )
        )
        session.add(
            McpServerRegistry(
                server_id="server-1",
                name="matched-indicator",
                url="https://matched-indicator.test",
            )
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat-intel/unmatched-indicators",
        params={"limit": 20},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["by_type"]
    assert {item["total"] for item in payload["by_type"]} == {1, 2}
    assert sum(item["unmatched"] for item in payload["by_type"]) >= 1
    assert payload["recent_unmatched"]
    print("PASS")