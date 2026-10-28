from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_scan_freshness_distribution


class ScanFreshnessBuckets(BaseModel):
    never_scanned: int
    under_24h: int
    one_to_seven_days: int
    over_seven_days: int


class RegistrySourceCount(BaseModel):
    registry_source: str
    count: int


class ScanFreshnessResponse(BaseModel):
    total: int
    buckets: ScanFreshnessBuckets
    stale_seen_count: int
    by_source: List[RegistrySourceCount]


router = APIRouter(prefix="/api", tags=["registry"])


@router.get(
    "/registry/scan-freshness",
    response_model=ScanFreshnessResponse,
    summary="Get scan freshness distribution for registered MCP servers",
)
def get_scan_freshness(
    session: Session = Depends(get_session),
) -> ScanFreshnessResponse:
    result = get_scan_freshness_distribution(session)
    return ScanFreshnessResponse(**result)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

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
            server_id="freshness-test-1",
            name="Never scanned",
            risk_tier="unknown",
            first_seen=now - timedelta(days=40),
            last_seen=now - timedelta(days=1),
            last_scanned=None,
            scan_count=0,
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="freshness-test-2",
            name="Recently scanned",
            risk_tier="low",
            first_seen=now - timedelta(days=10),
            last_seen=now - timedelta(hours=1),
            last_scanned=now - timedelta(hours=2),
            scan_count=2,
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="freshness-test-3",
            name="Three days old",
            risk_tier="medium",
            first_seen=now - timedelta(days=10),
            last_seen=now - timedelta(days=3),
            last_scanned=now - timedelta(days=3),
            scan_count=1,
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="freshness-test-4",
            name="Thirty days old",
            risk_tier="high",
            first_seen=now - timedelta(days=60),
            last_seen=now - timedelta(days=30),
            last_scanned=now - timedelta(days=30),
            scan_count=1,
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="freshness-test-5",
            name="Stale seen",
            risk_tier="high",
            first_seen=now - timedelta(days=60),
            last_seen=now - timedelta(days=40),
            last_scanned=now - timedelta(days=30),
            scan_count=1,
            registry_source="test",
        ),
    ]

    db = TestingSession()
    db.add_all(rows)
    db.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-freshness")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5, payload
    assert payload["buckets"]["never_scanned"] == 1, payload
    assert payload["buckets"]["under_24h"] == 1, payload
    assert payload["buckets"]["one_to_seven_days"] == 1, payload
    assert payload["buckets"]["over_seven_days"] == 2, payload
    assert payload["stale_seen_count"] == 1, payload
    assert payload["by_source"] == [{"registry_source": "test", "count": 5}], payload

    db.close()
    engine.dispose()
    print("PASS")