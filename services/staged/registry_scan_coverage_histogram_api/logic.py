from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry


MAX_MATERIALISED_ROWS = 10000


class ScanCoverageBucket(BaseModel):
    scan_count: int
    count: int


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    servers: int
    never_scanned: int
    avg_scan_count: float


class ScanCoverageResponse(BaseModel):
    total_servers: int
    never_scanned: int
    scanned_once: int
    buckets: List[ScanCoverageBucket]
    stale_scanned: int
    stale_threshold_days: int
    by_source: List[RegistrySourceCoverage]


router = APIRouter(prefix="/api")


def _normalise_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get(
    "/registry/scan-coverage",
    response_model=ScanCoverageResponse,
    summary="Registry scan coverage",
)
def get_scan_coverage(
    bucket_size: int = Query(1, ge=1, le=10),
    stale_days: int = Query(30, ge=1, le=365),
    db: Any = Depends(get_session),
) -> ScanCoverageResponse:
    rows = (
        db.query(McpServerRegistry)
        .order_by(
            McpServerRegistry.scan_count.desc(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(MAX_MATERIALISED_ROWS)
        .all()
    )

    total_servers = len(rows)
    never_scanned = 0
    scanned_once = 0
    stale_scanned = 0
    bucket_counts: Dict[int, int] = {}
    source_counts: Dict[str, Dict[str, float]] = {}
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    for row in rows:
        scan_count = int(row.scan_count or 0)
        source = str(row.registry_source) if row.registry_source is not None else "unknown"

        if scan_count == 0:
            never_scanned += 1
        elif scan_count == 1:
            scanned_once += 1

        bucket = (scan_count // bucket_size) * bucket_size
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        if scan_count > 0:
            last_scanned = _normalise_datetime(row.last_scanned)
            if last_scanned is None or last_scanned < stale_cutoff:
                stale_scanned += 1

        source_aggregate = source_counts.setdefault(
            source,
            {"servers": 0, "never_scanned": 0, "scan_count_total": 0},
        )
        source_aggregate["servers"] += 1
        source_aggregate["scan_count_total"] += scan_count
        if scan_count == 0:
            source_aggregate["never_scanned"] += 1

    buckets = [
        ScanCoverageBucket(scan_count=scan_count, count=count)
        for scan_count, count in sorted(bucket_counts.items())
    ]

    by_source = [
        RegistrySourceCoverage(
            registry_source=source,
            servers=int(aggregate["servers"]),
            never_scanned=int(aggregate["never_scanned"]),
            avg_scan_count=(
                float(aggregate["scan_count_total"]) / float(aggregate["servers"])
                if aggregate["servers"]
                else 0.0
            ),
        )
        for source, aggregate in sorted(source_counts.items())
    ]

    return ScanCoverageResponse(
        total_servers=total_servers,
        never_scanned=never_scanned,
        scanned_once=scanned_once,
        buckets=buckets,
        stale_scanned=stale_scanned,
        stale_threshold_days=stale_days,
        by_source=by_source,
    )


app = FastAPI(title="Registry Scan Coverage API")
app.include_router(router)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    session = TestSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="scan-zero",
                name="scan-zero",
                registry_source="source-a",
                scan_count=0,
                last_scanned=None,
                last_seen=now,
                risk_tier="low",
                trust_score=50,
            ),
            McpServerRegistry(
                server_id="scan-one",
                name="scan-one",
                registry_source="source-b",
                scan_count=1,
                last_scanned=now,
                last_seen=now,
                risk_tier="medium",
                trust_score=60,
            ),
            McpServerRegistry(
                server_id="scan-five-a",
                name="scan-five-a",
                registry_source="source-a",
                scan_count=5,
                last_scanned=now,
                last_seen=now,
                risk_tier="high",
                trust_score=80,
            ),
            McpServerRegistry(
                server_id="scan-five-b",
                name="scan-five-b",
                registry_source="source-b",
                scan_count=5,
                last_scanned=now,
                last_seen=now,
                risk_tier="high",
                trust_score=80,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        override_db = TestSession()
        try:
            yield override_db
        finally:
            override_db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_session

    response = TestClient(that_app).get(
        "/api/registry/scan-coverage",
        params={"bucket_size": 1, "stale_days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert payload["never_scanned"] == 1
    assert payload["scanned_once"] == 1
    assert any(
        bucket["scan_count"] == 5 and bucket["count"] == 2
        for bucket in payload["buckets"]
    )
    sources = {
        item["registry_source"]: item
        for item in payload["by_source"]
    }
    assert sources["source-a"]["servers"] == 2
    assert sources["source-a"]["never_scanned"] == 1
    assert sources["source-a"]["avg_scan_count"] == 2.5
    assert sources["source-b"]["servers"] == 2
    assert sources["source-b"]["never_scanned"] == 0
    assert sources["source-b"]["avg_scan_count"] == 3.0

    print("PASS")