from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_registry as _get_registry
except ImportError:
    try:
        from .logic import get_pulse_registry as _get_registry
    except ImportError:
        from .logic import get_threat_intel_pulse_registry as _get_registry


class PulseRegistryEntry(BaseModel):
    pulse_id: str
    pulse_name: str
    pulse_created: Optional[datetime] = None
    indicator_count: int
    aggregator_count: int
    source_count: int


class ThreatIntelPulseRegistryResponse(BaseModel):
    pulses: List[PulseRegistryEntry]
    total_pulses: int
    total_indicators: int


router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threat/pulses/registry",
    response_model=ThreatIntelPulseRegistryResponse,
)
def get_threat_intel_pulse_registry(
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _get_registry(session=session, source=source, limit=limit)


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
    test_rows = [
        {
            "fetched_at": now,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.1",
            "is_aggregator": True,
            "pulse_created": now,
            "pulse_id": "pulse-alpha",
            "pulse_name": "Alpha Pulse",
            "source": "alpha",
            "source_url": "https://alpha.example/1",
        },
        {
            "fetched_at": now,
            "indicator_type": "domain",
            "indicator_value": "alpha.example",
            "is_aggregator": False,
            "pulse_created": now,
            "pulse_id": "pulse-alpha",
            "pulse_name": "Alpha Pulse",
            "source": "alpha",
            "source_url": "https://alpha.example/2",
        },
        {
            "fetched_at": now,
            "indicator_type": "url",
            "indicator_value": "https://beta.example/feed",
            "is_aggregator": False,
            "pulse_created": now,
            "pulse_id": "pulse-alpha",
            "pulse_name": "Alpha Pulse",
            "source": "beta",
            "source_url": "https://beta.example/feed",
        },
        {
            "fetched_at": now,
            "indicator_type": "hash",
            "indicator_value": "a" * 64,
            "is_aggregator": True,
            "pulse_created": now,
            "pulse_id": "pulse-beta",
            "pulse_name": "Beta Pulse",
            "source": "alpha",
            "source_url": "https://alpha.example/3",
        },
        {
            "fetched_at": now,
            "indicator_type": "cve",
            "indicator_value": "CVE-2025-0001",
            "is_aggregator": False,
            "pulse_created": now,
            "pulse_id": "pulse-beta",
            "pulse_name": "Beta Pulse",
            "source": "beta",
            "source_url": "https://beta.example/cve",
        },
    ]

    with test_engine.begin() as connection:
        connection.execute(ThreatIntelRef.__table__.insert(), test_rows)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Session:
        return TestSession()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat/pulses/registry")
    response.raise_for_status()
    payload = response.json()

    assert payload["total_pulses"] == 2
    assert sum(pulse["indicator_count"] for pulse in payload["pulses"]) == 5
    assert any(pulse["source_count"] == 2 for pulse in payload["pulses"])
    print("PASS")