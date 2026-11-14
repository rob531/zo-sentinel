"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the scan-count distribution service. The router is
intentionally thin: it injects the application database session and delegates
all aggregation to the service logic module.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import scan_count_distribution
except ImportError:
    from .logic import get_scan_count_distribution as scan_count_distribution


router = APIRouter(prefix="/api", tags=["server_scan_count_distribution"])


class ScanCountBucket(BaseModel):
    bucket: str
    server_count: int
    never_scanned: int


class SourceScanCountBucket(BaseModel):
    bucket: str
    server_count: int


class SourceScanCountBreakdown(BaseModel):
    registry_source: str
    buckets: list[SourceScanCountBucket]


class TopScannedServer(BaseModel):
    server_id: str
    scan_count: int
    last_scanned: str | None


class ScanCountDistributionResponse(BaseModel):
    total_servers: int
    buckets: list[ScanCountBucket]
    by_source: list[SourceScanCountBreakdown]
    top_scanned: list[TopScannedServer]


def _serialize_last_scanned(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    top_scanned = []
    for server in normalized.get("top_scanned", []):
        server = dict(server)
        server["last_scanned"] = _serialize_last_scanned(server.get("last_scanned"))
        top_scanned.append(server)
    normalized["top_scanned"] = top_scanned
    return normalized


@router.get(
    "/registry/scan-count-buckets",
    response_model=ScanCountDistributionResponse,
)
def get_scan_count_buckets(
    db: Session = Depends(get_session),
) -> ScanCountDistributionResponse:
    payload = scan_count_distribution(db)
    return ScanCountDistributionResponse(**_normalize_payload(payload))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_db = TestingSessionLocal()
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="scan-zero",
                scan_count=0,
                last_scanned=None,
                registry_source="source-a",
            ),
            McpServerRegistry(
                server_id="scan-one",
                scan_count=1,
                last_scanned=datetime(2024, 1, 1),
                registry_source="source-a",
            ),
            McpServerRegistry(
                server_id="scan-five",
                scan_count=5,
                last_scanned=datetime(2024, 1, 2),
                registry_source="source-b",
            ),
            McpServerRegistry(
                server_id="scan-twelve",
                scan_count=12,
                last_scanned=datetime(2024, 1, 3),
                registry_source="source-b",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get("/api/registry/scan-count-buckets")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_servers"] == 4
    zero_bucket = next(item for item in body["buckets"] if item["bucket"] == "0")
    assert zero_bucket["server_count"] == 1
    assert zero_bucket["never_scanned"] == 1
    assert len(body["by_source"]) >= 2
    print("PASS")