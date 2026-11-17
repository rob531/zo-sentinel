from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class ScanCountBucket(BaseModel):
    bucket: str
    count: int


class RegistrySourceSummary(BaseModel):
    registry_source: str
    servers: int
    avg_scan_count: float


class ScanCountBucketsResponse(BaseModel):
    buckets: list[ScanCountBucket]
    by_source: list[RegistrySourceSummary]
    stale_scan_count: int
    total_servers: int
    generated_at: datetime


router = APIRouter(prefix="/api")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_scan_count_buckets(session: Session) -> ScanCountBucketsResponse:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
        )
    ).all()

    bucket_counts = {"0": 0, "1": 0, "2-3": 0, "4-9": 0, "10+": 0}
    source_counts: dict[str, int] = defaultdict(int)
    source_scan_totals: dict[str, int] = defaultdict(int)
    scan_dates: list[datetime] = []

    for row in rows:
        scan_count = row.scan_count if row.scan_count is not None else 0
        if scan_count == 0:
            bucket_counts["0"] += 1
        elif scan_count == 1:
            bucket_counts["1"] += 1
        elif scan_count <= 3:
            bucket_counts["2-3"] += 1
        elif scan_count <= 9:
            bucket_counts["4-9"] += 1
        else:
            bucket_counts["10+"] += 1

        source = row.registry_source or "unknown"
        source_counts[source] += 1
        source_scan_totals[source] += scan_count

        if row.last_scanned is not None:
            scan_dates.append(_utc(row.last_scanned))

    stale_scan_count = 0
    if scan_dates:
        stale_before = max(scan_dates) - timedelta(days=30)
        stale_scan_count = sum(1 for value in scan_dates if value < stale_before)

    by_source = [
        RegistrySourceSummary(
            registry_source=source,
            servers=source_counts[source],
            avg_scan_count=source_scan_totals[source] / source_counts[source],
        )
        for source in sorted(source_counts)
    ]

    return ScanCountBucketsResponse(
        buckets=[
            ScanCountBucket(bucket=bucket, count=count)
            for bucket, count in bucket_counts.items()
        ],
        by_source=by_source,
        stale_scan_count=stale_scan_count,
        total_servers=len(rows),
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/registry/scan-count-buckets",
    response_model=ScanCountBucketsResponse,
)
def get_scan_count_buckets(
    session: Session = Depends(get_session),
) -> ScanCountBucketsResponse:
    return compute_scan_count_buckets(session)


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-server-0",
                    name="Contract Server 0",
                    registry_source="contract-source-a",
                    scan_count=0,
                    last_scanned=now - timedelta(days=45),
                    last_seen=now,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="contract-server-1",
                    name="Contract Server 1",
                    registry_source="contract-source-a",
                    scan_count=1,
                    last_scanned=now - timedelta(days=20),
                    last_seen=now,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="contract-server-2",
                    name="Contract Server 2",
                    registry_source="contract-source-b",
                    scan_count=5,
                    last_scanned=now - timedelta(days=10),
                    last_seen=now,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="contract-server-3",
                    name="Contract Server 3",
                    registry_source="contract-source-b",
                    scan_count=12,
                    last_scanned=now,
                    last_seen=now,
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-count-buckets")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4, payload
        buckets = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert buckets.get("10+") == 1, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()