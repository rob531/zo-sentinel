"""router.py -- the registry scan coverage service's HTTP surface.

Exposes `router` for the API spine and delegates scan-coverage aggregation to
`logic.py`. The database session is injected through the application's real
session dependency.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import scan_coverage
except ImportError:
    # The staged service may be evaluated as a single file. Keep the fallback
    # on the same authoritative ORM data layer rather than creating a local
    # database implementation.
    def _scan_coverage(db: Session) -> dict[str, Any]:
        rows = db.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.registry_source,
                McpServerRegistry.first_seen,
                McpServerRegistry.last_seen,
                McpServerRegistry.last_scanned,
                McpServerRegistry.scan_count,
                McpServerRegistry.risk_tier,
            )
        ).all()

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        buckets: dict[str, dict[str, Any]] = {}

        for row in rows:
            source = str(row.registry_source) if row.registry_source is not None else ""
            bucket = buckets.setdefault(
                source,
                {
                    "registry_source": source,
                    "total": 0,
                    "scanned": 0,
                    "never_scanned": 0,
                    "stale": 0,
                    "scan_count_sum": 0,
                },
            )
            bucket["total"] += 1
            scan_count = int(row.scan_count or 0)
            bucket["scan_count_sum"] += scan_count

            if row.last_scanned is None:
                bucket["never_scanned"] += 1
            else:
                bucket["scanned"] += 1
                last_scanned = row.last_scanned
                if last_scanned.tzinfo is not None:
                    last_scanned = last_scanned.astimezone(timezone.utc).replace(
                        tzinfo=None
                    )
                if last_scanned < cutoff:
                    bucket["stale"] += 1

        sources = []
        for bucket in buckets.values():
            total = bucket["total"]
            sources.append(
                {
                    "registry_source": bucket["registry_source"],
                    "total": total,
                    "scanned": bucket["scanned"],
                    "never_scanned": bucket["never_scanned"],
                    "stale": bucket["stale"],
                    "avg_scan_count": round(bucket["scan_count_sum"] / total, 2)
                    if total
                    else 0.0,
                }
            )

        return {
            "total": sum(bucket["total"] for bucket in buckets.values()),
            "scanned": sum(bucket["scanned"] for bucket in buckets.values()),
            "never_scanned": sum(
                bucket["never_scanned"] for bucket in buckets.values()
            ),
            "stale": sum(bucket["stale"] for bucket in buckets.values()),
            "sources": sources,
        }

    def scan_coverage(db: Session) -> dict[str, Any]:
        return _scan_coverage(db)


router = APIRouter(prefix="/api/registry", tags=["registry"])


class SourceScanCoverage(BaseModel):
    registry_source: str
    total: int
    scanned: int
    never_scanned: int
    stale: int
    avg_scan_count: float


class ScanCoverageResponse(BaseModel):
    total: int
    scanned: int
    never_scanned: int
    stale: int
    sources: list[SourceScanCoverage]


@router.get("/scan-coverage", response_model=ScanCoverageResponse)
def get_scan_coverage(db: Session = Depends(get_session)) -> ScanCoverageResponse:
    return ScanCoverageResponse(**scan_coverage(db))


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session as app_get_session
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(days=8)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="scan-test-recent",
                    name="recent",
                    registry_source="alpha",
                    first_seen=now - timedelta(days=2),
                    last_seen=now,
                    last_scanned=now,
                    scan_count=4,
                ),
                McpServerRegistry(
                    server_id="scan-test-never",
                    name="never",
                    registry_source="alpha",
                    first_seen=now - timedelta(days=2),
                    last_seen=now,
                    last_scanned=None,
                    scan_count=None,
                ),
                McpServerRegistry(
                    server_id="scan-test-old",
                    name="old",
                    registry_source="beta",
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=old,
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[app_get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-coverage")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3, payload
    assert payload["never_scanned"] == 1, payload

    buckets = {item["registry_source"]: item for item in payload["sources"]}
    assert buckets["alpha"]["total"] == 2, payload
    assert buckets["alpha"]["scanned"] == 1, payload
    assert buckets["alpha"]["never_scanned"] == 1, payload
    assert buckets["beta"]["total"] == 1, payload
    assert buckets["beta"]["stale"] == 1, payload


if __name__ == "__main__":
    _self_test()
    print("PASS")