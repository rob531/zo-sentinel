from __future__ import annotations

from datetime import timedelta
from typing import Dict

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ScanRecencyBucket(BaseModel):
    bucket: str
    count: int


class RegistryLastScanRecencyResponse(BaseModel):
    buckets: list[ScanRecencyBucket]
    total: int
    never_scanned: int
    max_staleness_days: float | None


_BUCKET_NAMES = (
    "never_scanned",
    "lt_1h",
    "lt_24h",
    "lt_7d",
    "gte_7d",
)


def get_last_scan_recency(
    session: Session = Depends(get_session),
) -> RegistryLastScanRecencyResponse:
    table_name = McpServerRegistry.__tablename__
    dialect = session.get_bind().dialect.name

    if dialect == "postgresql":
        bucket_sql = f"""
            SELECT
                CASE
                    WHEN last_scanned IS NULL THEN 'never_scanned'
                    WHEN last_scanned >= now() - INTERVAL '1 hour' THEN 'lt_1h'
                    WHEN last_scanned >= now() - INTERVAL '24 hours' THEN 'lt_24h'
                    WHEN last_scanned >= now() - INTERVAL '7 days' THEN 'lt_7d'
                    ELSE 'gte_7d'
                END AS bucket,
                COUNT(*) AS count
            FROM {table_name}
            WHERE server_id IS NOT NULL
            GROUP BY 1
        """
        max_staleness_sql = f"""
            SELECT
                MAX(
                    CASE
                        WHEN last_scanned IS NOT NULL THEN
                            GREATEST(
                                0,
                                EXTRACT(EPOCH FROM (now() - last_scanned)) / 86400.0
                            )
                        ELSE NULL
                    END
                ) AS max_staleness_days
            FROM {table_name}
            WHERE server_id IS NOT NULL
        """
    else:
        bucket_sql = f"""
            SELECT
                CASE
                    WHEN last_scanned IS NULL THEN 'never_scanned'
                    WHEN last_scanned >= datetime('now', '-1 hour') THEN 'lt_1h'
                    WHEN last_scanned >= datetime('now', '-24 hours') THEN 'lt_24h'
                    WHEN last_scanned >= datetime('now', '-7 days') THEN 'lt_7d'
                    ELSE 'gte_7d'
                END AS bucket,
                COUNT(*) AS count
            FROM {table_name}
            WHERE server_id IS NOT NULL
            GROUP BY 1
        """
        max_staleness_sql = f"""
            SELECT
                MAX(
                    CASE
                        WHEN last_scanned IS NOT NULL THEN
                            MAX(
                                0,
                                (julianday('now') - julianday(last_scanned)) * 86400.0
                            )
                        ELSE NULL
                    END
                ) AS max_staleness_days
            FROM {table_name}
            WHERE server_id IS NOT NULL
        """

    counts: Dict[str, int] = {bucket: 0 for bucket in _BUCKET_NAMES}
    for row in session.execute(text(bucket_sql)):
        counts[row.bucket] = int(row.count)

    total = sum(counts.values())
    never_scanned = counts["never_scanned"]
    max_staleness = session.execute(text(max_staleness_sql)).scalar()

    return RegistryLastScanRecencyResponse(
        buckets=[
            ScanRecencyBucket(bucket=bucket, count=counts[bucket])
            for bucket in _BUCKET_NAMES
        ],
        total=total,
        never_scanned=never_scanned,
        max_staleness_days=(
            round(float(max_staleness), 6) if max_staleness is not None else None
        ),
    )


get_registry_last_scan_recency = get_last_scan_recency


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    last_scanned TEXT,
                    last_seen TEXT,
                    scan_count INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    registry_source,
                    last_scanned,
                    last_seen,
                    scan_count
                )
                VALUES
                    (
                        'fresh-server',
                        'Fresh Server',
                        'test',
                        datetime('now'),
                        datetime('now'),
                        1
                    ),
                    (
                        'stale-server',
                        'Stale Server',
                        'test',
                        datetime('now', '-2 days'),
                        datetime('now', '-2 days'),
                        1
                    ),
                    (
                        'never-scanned-server-1',
                        'Never Scanned Server 1',
                        'test',
                        NULL,
                        NULL,
                        0
                    ),
                    (
                        'never-scanned-server-2',
                        'Never Scanned Server 2',
                        'test',
                        NULL,
                        NULL,
                        0
                    )
                """
            )
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get(
        "/api/registry/last-scan-recency",
        response_model=RegistryLastScanRecencyResponse,
    )
    def last_scan_recency_endpoint(
        response: RegistryLastScanRecencyResponse = Depends(get_last_scan_recency),
    ) -> RegistryLastScanRecencyResponse:
        return response

    with TestClient(test_app) as client:
        response = client.get("/api/registry/last-scan-recency")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["never_scanned"] == 2
        bucket_counts = {
            item["bucket"]: item["count"] for item in payload["buckets"]
        }
        assert bucket_counts["lt_1h"] == 1
        assert bucket_counts["lt_24h"] == 0
        assert bucket_counts["lt_7d"] == 1
        assert bucket_counts["gte_7d"] == 0
        assert bucket_counts["never_scanned"] == 2
        assert payload["max_staleness_days"] is not None
        assert payload["max_staleness_days"] >= 1.99

    engine.dispose()
    print("PASS")