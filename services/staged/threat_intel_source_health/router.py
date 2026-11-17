"""HTTP surface for threat intelligence source health."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_source_health

router = APIRouter(prefix="/api", tags=["threat-intel"])


class SourceHealth(BaseModel):
    source: str
    indicators: int
    pulses: int
    last_fetch: str | None
    age_hours: float | None
    is_aggregator: bool


class SourceHealthResponse(BaseModel):
    sources: list[SourceHealth]
    total_sources: int


@router.get("/threat-intel/sources", response_model=SourceHealthResponse)
def list_source_health(db: Session = Depends(get_session)) -> SourceHealthResponse:
    sources = get_source_health(db)
    return SourceHealthResponse(
        sources=[SourceHealth(**source) for source in sources],
        total_sources=len(sources),
    )


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ThreatIntelRef

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse 1",
                    pulse_created=datetime.now(timezone.utc),
                    is_aggregator=False,
                    source="source-alpha",
                    source_url="https://alpha.example/feed",
                    fetched_at=datetime.now(timezone.utc),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-2",
                    pulse_name="Pulse 2",
                    pulse_created=datetime.now(timezone.utc),
                    is_aggregator=False,
                    source="source-alpha",
                    source_url="https://alpha.example/feed",
                    fetched_at=datetime.now(timezone.utc),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id="pulse-3",
                    pulse_name="Pulse 3",
                    pulse_created=datetime.now(timezone.utc),
                    is_aggregator=True,
                    source="aggregator-beta",
                    source_url="https://beta.example/feed",
                    fetched_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    response = TestClient(test_app).get("/api/threat-intel/sources")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_sources"] == 2
    assert len(payload["sources"]) == 2
    assert any(
        source["source"] == "aggregator-beta" and source["is_aggregator"] is True
        for source in payload["sources"]
    )
    print("PASS")