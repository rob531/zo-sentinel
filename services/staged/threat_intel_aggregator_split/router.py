"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_source_split
except ImportError:  # pragma: no cover - supports the alternate logic name.
    from .logic import source_split as get_source_split

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class SourceCount(BaseModel):
    source: str
    count: int


class SourceSplitResponse(BaseModel):
    days: int
    total_refs: int
    aggregator_count: int
    direct_count: int
    aggregator_ratio: float
    by_indicator_type: dict[str, int]
    top_sources: list[SourceCount]
    freshness_buckets: dict[str, int]


@router.get("/source-split", response_model=SourceSplitResponse)
def get_source_split_response(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> SourceSplitResponse:
    result = get_source_split(db, days)
    payload = dict(result)

    payload.setdefault("days", days)
    payload.setdefault(
        "total_refs",
        payload.get("aggregator_count", 0) + payload.get("direct_count", 0),
    )
    payload.setdefault("aggregator_count", 0)
    payload.setdefault("direct_count", 0)
    payload.setdefault(
        "aggregator_ratio",
        (
            payload["aggregator_count"] / payload["total_refs"]
            if payload["total_refs"]
            else 0.0
        ),
    )
    payload.setdefault("by_indicator_type", {})
    payload.setdefault("top_sources", [])
    payload.setdefault("freshness_buckets", {})

    return SourceSplitResponse(**payload)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=now - timedelta(days=1),
                    is_aggregator=True,
                    source="aggregator-one",
                    source_url="https://example.invalid/1",
                    fetched_at=now - timedelta(hours=2),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=now - timedelta(days=2),
                    is_aggregator=True,
                    source="aggregator-two",
                    source_url="https://example.invalid/2",
                    fetched_at=now - timedelta(days=3),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-3",
                    pulse_name="Pulse Three",
                    pulse_created=now - timedelta(days=3),
                    is_aggregator=False,
                    source="direct-one",
                    source_url="https://example.invalid/3",
                    fetched_at=now - timedelta(days=5),
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="hash",
                    indicator_value="a" * 64,
                    pulse_id="pulse-4",
                    pulse_name="Pulse Four",
                    pulse_created=now - timedelta(days=4),
                    is_aggregator=False,
                    source="direct-two",
                    source_url="https://example.invalid/4",
                    fetched_at=now - timedelta(days=40),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/source-split", params={"days": 365})
        assert response.status_code == 200
        body = response.json()
        assert body["total_refs"] == 4
        assert body["aggregator_count"] == 2
        assert body["direct_count"] == 2
        assert len(body["by_indicator_type"]) >= 1

    print("PASS")