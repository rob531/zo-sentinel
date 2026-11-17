from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_scan_recency

router = APIRouter(prefix="/api", tags=["registry"])


class RegistrySourceScanRecency(BaseModel):
    registry_source: str
    total: int
    never_scanned: int
    stale: int
    avg_scan_count: float


class RegistryRecencyBucket(BaseModel):
    bucket: str
    count: int


class RegistryScanRecencyResponse(BaseModel):
    days: int
    total_servers: int
    never_scanned: int
    stale_scanned: int
    coverage_pct: float
    by_source: List[RegistrySourceScanRecency]
    by_risk_tier: Dict[str, int]
    buckets: List[RegistryRecencyBucket]


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("scan recency logic did not return a mapping")


@router.get(
    "/registry/scan-recency",
    response_model=RegistryScanRecencyResponse,
)
async def registry_scan_recency(
    days: int = Query(default=14, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    result = get_scan_recency(session, days)
    if asyncio.iscoroutine(result):
        result = await result

    payload = _as_dict(result)
    total = int(payload.get("total_servers", 0))
    never_scanned = int(payload.get("never_scanned", 0))
    stale_scanned = int(payload.get("stale_scanned", 0))

    payload["days"] = days
    payload["total_servers"] = total
    payload["never_scanned"] = never_scanned
    payload["stale_scanned"] = stale_scanned
    payload["coverage_pct"] = (
        round(
            max(total - never_scanned - stale_scanned, 0) * 100.0 / total,
            2,
        )
        if total
        else 0.0
    )
    payload.setdefault("by_source", [])
    payload.setdefault("by_risk_tier", {})
    payload.setdefault(
        "buckets",
        [
            {"bucket": "never", "count": 0},
            {"bucket": "<=1d", "count": 0},
            {"bucket": "1-7d", "count": 0},
            {"bucket": "7-30d", "count": 0},
            {"bucket": "30-90d", "count": 0},
            {"bucket": ">90d", "count": 0},
        ],
    )
    return payload


def _self_test() -> None:
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
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    rows = [
        McpServerRegistry(
            server_id="self-test-never",
            name="Never scanned",
            registry_source="catalog",
            risk_tier="tier-3",
            first_seen=now - timedelta(days=90),
            last_seen=now,
            last_scanned=None,
            scan_count=0,
            trust_score=50,
            confidence=50,
        ),
        McpServerRegistry(
            server_id="self-test-stale",
            name="Stale",
            registry_source="runtime",
            risk_tier="tier-2",
            first_seen=now - timedelta(days=90),
            last_seen=now,
            last_scanned=now - timedelta(days=60),
            scan_count=2,
            trust_score=70,
            confidence=70,
        ),
        McpServerRegistry(
            server_id="self-test-fresh-1",
            name="Fresh one",
            registry_source="catalog",
            risk_tier="tier-1",
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=now - timedelta(days=2),
            scan_count=4,
            trust_score=90,
            confidence=90,
        ),
        McpServerRegistry(
            server_id="self-test-fresh-2",
            name="Fresh two",
            registry_source="runtime",
            risk_tier="tier-2",
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=now - timedelta(days=3),
            scan_count=3,
            trust_score=85,
            confidence=85,
        ),
    ]

    with TestingSession() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-recency")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 4, payload
    assert payload["never_scanned"] == 1, payload
    assert payload["stale_scanned"] == 1, payload
    assert payload["coverage_pct"] == 50.0, payload
    print("PASS")


if __name__ == "__main__":
    _self_test()