from __future__ import annotations

from typing import Any

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = ("0", "1", "2-3", "4-9", "10+")


def bucket_scan_count(n: int | None) -> str:
    """Map a scan count to its distribution bucket."""
    if n is None or n <= 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 3:
        return "2-3"
    if n <= 9:
        return "4-9"
    return "10+"


def compute_scan_buckets(rows: list[dict]) -> dict:
    """Compute the scan-count distribution for registry rows."""
    bucket_counts = {bucket: 0 for bucket in BUCKETS}
    by_source: dict[str, dict[str, int]] = {}
    never_scanned = 0

    for row in rows:
        if hasattr(row, "_mapping"):
            values = dict(row._mapping)
        elif isinstance(row, dict):
            values = row
        else:
            values = dict(row)

        bucket = bucket_scan_count(values.get("scan_count"))
        bucket_counts[bucket] += 1

        source = values.get("registry_source")
        if source is None:
            source = "unknown"
        else:
            source = str(source)

        source_counts = by_source.setdefault(
            source, {bucket_name: 0 for bucket_name in BUCKETS}
        )
        source_counts[bucket] += 1

        if values.get("last_scanned") is None:
            never_scanned += 1

    return {
        "total": len(rows),
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in BUCKETS
        ],
        "by_source": by_source,
        "never_scanned": never_scanned,
    }


def get_scan_count_distribution(session: Any) -> dict:
    """Read the authoritative registry rows and compute their distribution."""
    rows = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.scan_count,
        McpServerRegistry.last_scanned,
    ).all()

    return compute_scan_buckets([dict(row._mapping) for row in rows])


def compute_scan_count_distribution(session: Any) -> dict:
    """Compatibility alias for callers that use the longer function name."""
    return get_scan_count_distribution(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI
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

    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    now = datetime.now(timezone.utc)
    test_session.query(McpServerRegistry).delete()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="server-null",
                registry_source="source-a",
                scan_count=None,
                last_scanned=None,
            ),
            McpServerRegistry(
                server_id="server-zero",
                registry_source="source-a",
                scan_count=0,
                last_scanned=now,
            ),
            McpServerRegistry(
                server_id="server-one",
                registry_source="source-b",
                scan_count=1,
                last_scanned=now,
            ),
            McpServerRegistry(
                server_id="server-three",
                registry_source="source-b",
                scan_count=3,
                last_scanned=now,
            ),
            McpServerRegistry(
                server_id="server-five",
                registry_source="source-a",
                scan_count=5,
                last_scanned=now,
            ),
            McpServerRegistry(
                server_id="server-twelve",
                registry_source="source-b",
                scan_count=12,
                last_scanned=now,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: test_session

    @test_app.get("/api/registry/scan-count-distribution")
    def scan_count_distribution_endpoint(session=Depends(get_session)):
        return get_scan_count_distribution(session)

    response = TestClient(test_app).get(
        "/api/registry/scan-count-distribution"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert sum(item["count"] for item in payload["buckets"]) == 6
    assert payload["never_scanned"] == 1
    assert payload["buckets"] == [
        {"bucket": "0", "count": 2},
        {"bucket": "1", "count": 1},
        {"bucket": "2-3", "count": 1},
        {"bucket": "4-9", "count": 1},
        {"bucket": "10+", "count": 1},
    ]

    test_session.close()
    print("PASS")