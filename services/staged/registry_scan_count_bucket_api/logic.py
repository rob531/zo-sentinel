from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_ORDER = ("0", "1", "2-3", "4-9", "10+")
_BUCKET_CASE = (
    case(
        (func.coalesce(McpServerRegistry.scan_count, 0) <= 0, "0"),
        (func.coalesce(McpServerRegistry.scan_count, 0) == 1, "1"),
        (func.coalesce(McpServerRegistry.scan_count, 0) <= 3, "2-3"),
        (func.coalesce(McpServerRegistry.scan_count, 0) <= 9, "4-9"),
        else_="10+",
    )
)


def get_registry_scan_count_buckets(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bucket_rows = session.execute(
        select(_BUCKET_CASE.label("bucket"), func.count().label("server_count"))
        .select_from(McpServerRegistry)
        .group_by(_BUCKET_CASE)
    ).all()
    bucket_counts = {bucket: 0 for bucket in _BUCKET_ORDER}
    bucket_counts.update(
        {
            str(row.bucket): int(row.server_count)
            for row in bucket_rows
            if str(row.bucket) in bucket_counts
        }
    )

    source_rows = session.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("servers"),
            func.avg(McpServerRegistry.scan_count).label("avg_scan_count"),
        )
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    newest_scanned = session.execute(
        select(func.max(McpServerRegistry.last_scanned))
    ).scalar()
    stale_scan_count = 0
    if newest_scanned is not None:
        stale_cutoff = newest_scanned - timedelta(days=30)
        stale_scan_count = int(
            session.execute(
                select(func.count())
                .select_from(McpServerRegistry)
                .where(McpServerRegistry.last_scanned < stale_cutoff)
            ).scalar_one()
        )

    total_servers = int(
        session.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar_one()
    )

    return {
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in _BUCKET_ORDER
        ],
        "by_source": [
            {
                "registry_source": row.registry_source,
                "servers": int(row.servers),
                "avg_scan_count": (
                    float(row.avg_scan_count)
                    if row.avg_scan_count is not None
                    else 0.0
                ),
            }
            for row in source_rows
        ],
        "stale_scan_count": stale_scan_count,
        "total_servers": total_servers,
        "generated_at": datetime.now(timezone.utc),
    }


get_scan_count_buckets = get_registry_scan_count_buckets


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="bucket-test-0",
                    name="Bucket Test 0",
                    registry_source="test-source",
                    scan_count=0,
                    last_scanned=base_time,
                    last_seen=base_time,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="bucket-test-1",
                    name="Bucket Test 1",
                    registry_source="test-source",
                    scan_count=1,
                    last_scanned=base_time + timedelta(days=1),
                    last_seen=base_time + timedelta(days=1),
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="bucket-test-5",
                    name="Bucket Test 5",
                    registry_source="test-source",
                    scan_count=5,
                    last_scanned=base_time + timedelta(days=2),
                    last_seen=base_time + timedelta(days=2),
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="bucket-test-12",
                    name="Bucket Test 12",
                    registry_source="test-source",
                    scan_count=12,
                    last_scanned=base_time + timedelta(days=3),
                    last_seen=base_time + timedelta(days=3),
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-count-buckets")
    def scan_count_buckets_endpoint(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_registry_scan_count_buckets(session=session)

    response = TestClient(test_app).get(
        "/api/registry/scan-count-buckets"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert any(
        bucket["bucket"] == "10+" and bucket["count"] == 1
        for bucket in payload["buckets"]
    )
    print("PASS")