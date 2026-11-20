"""HTTP surface for registry scan-lag distribution."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import scan_lag_distribution

router = APIRouter(prefix="/api/registry", tags=["registry"])


class BucketCount(BaseModel):
    bucket: str
    count: int


class RegistrySourceCount(BaseModel):
    registry_source: str
    total: int
    never_scanned: int


class RecentlyScannedServer(BaseModel):
    server_id: str
    name: str | None
    risk_tier: str | None
    last_scanned: datetime | None


class ScanLagDistributionResponse(BaseModel):
    buckets: list[BucketCount]
    never_scanned: int
    rescanned_total: int
    by_source: list[RegistrySourceCount]
    recent: list[RecentlyScannedServer]


@router.get("/scan-lag", response_model=ScanLagDistributionResponse)
def get_scan_lag_distribution(
    db: Session = Depends(get_session),
) -> ScanLagDistributionResponse:
    return ScanLagDistributionResponse(**scan_lag_distribution(db))


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-test-1",
                    name="Never scanned",
                    registry_source="source-a",
                    risk_tier="low",
                    trust_score=0.8,
                    first_seen=now,
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="registry-test-2",
                    name="Recently scanned",
                    registry_source="source-a",
                    risk_tier="medium",
                    trust_score=0.7,
                    first_seen=now - timedelta(days=10),
                    last_seen=now,
                    last_scanned=now - timedelta(days=1),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="registry-test-3",
                    name="Rescanned",
                    registry_source="source-b",
                    risk_tier="high",
                    trust_score=0.4,
                    first_seen=now - timedelta(days=120),
                    last_seen=now - timedelta(days=120),
                    last_scanned=now - timedelta(days=120),
                    scan_count=3,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-lag")
    payload = response.json()
    assert response.status_code == 200
    assert payload["never_scanned"] == 1
    assert len(payload["by_source"]) == 2
    assert sum(item["count"] for item in payload["buckets"]) == 3
    print("PASS")