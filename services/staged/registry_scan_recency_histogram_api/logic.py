"""logic.py -- the service's data/computation layer for registry scan recency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = (
    "never_scanned",
    "scanned_24h",
    "scanned_7d",
    "scanned_30d",
    "scanned_older",
)


def _as_utc(value: datetime) -> datetime:
    """Normalize timestamps returned by SQLite and Postgres consistently."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def scan_recency_histogram(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, object]:
    """Aggregate scan recency for the real mcp_server_registry table."""
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.risk_tier,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).all()
    now = datetime.now(timezone.utc)
    cutoffs = {
        "scanned_24h": now - timedelta(days=1),
        "scanned_7d": now - timedelta(days=7),
        "scanned_30d": now - timedelta(days=30),
    }
    counts = {bucket: 0 for bucket in BUCKETS}
    never_scanned_by_risk_tier: Dict[str, int] = {}
    scan_counts: List[int] = []
    zero_scan_count = 0

    for row in rows:
        last_scanned = row.last_scanned
        if last_scanned is None:
            bucket = "never_scanned"
            tier = row.risk_tier or "unknown"
            never_scanned_by_risk_tier[tier] = (
                never_scanned_by_risk_tier.get(tier, 0) + 1
            )
        else:
            scanned_at = _as_utc(last_scanned)
            if scanned_at >= cutoffs["scanned_24h"]:
                bucket = "scanned_24h"
            elif scanned_at >= cutoffs["scanned_7d"]:
                bucket = "scanned_7d"
            elif scanned_at >= cutoffs["scanned_30d"]:
                bucket = "scanned_30d"
            else:
                bucket = "scanned_older"

        counts[bucket] += 1

        if row.scan_count is not None:
            scan_count = int(row.scan_count)
            scan_counts.append(scan_count)
            if scan_count == 0:
                zero_scan_count += 1

    mean_scan_count = (
        float(sum(scan_counts) / len(scan_counts)) if scan_counts else 0.0
    )
    max_scan_count = max(scan_counts) if scan_counts else 0

    return {
        "registry_source": registry_source,
        "total_servers": len(rows),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]} for bucket in BUCKETS
        ],
        "never_scanned": counts["never_scanned"],
        "zero_scan_count": zero_scan_count,
        "mean_scan_count": mean_scan_count,
        "max_scan_count": max_scan_count,
        "never_scanned_by_risk_tier": never_scanned_by_risk_tier,
    }


# Keep the commonly used service-layer names available to thin routers.
get_scan_recency_histogram = scan_recency_histogram
compute_scan_recency_histogram = scan_recency_histogram
registry_scan_recency_histogram = scan_recency_histogram


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    from sqlalchemy.orm import sessionmaker

    TestSession = sessionmaker(bind=test_engine)
    with TestSession() as test_db:
        test_db.add_all(
            [
                McpServerRegistry(
                    server_id="never",
                    registry_source="test-source",
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="today",
                    registry_source="test-source",
                    last_scanned=datetime.now(timezone.utc),
                    scan_count=2,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="old",
                    registry_source="test-source",
                    last_scanned=datetime.now(timezone.utc) - timedelta(days=40),
                    scan_count=1,
                    risk_tier="medium",
                ),
            ]
        )
        test_db.commit()

        test_app = FastAPI()

        @test_app.get("/api/registry/scan-recency")
        def scan_recency_endpoint(
            registry_source: Optional[str] = None,
            db: Session = Depends(get_session),
        ):
            return scan_recency_histogram(db, registry_source)

        test_app.dependency_overrides[get_session] = lambda: test_db
        client = TestClient(test_app)
        response = client.get(
            "/api/registry/scan-recency",
            params={"registry_source": "test-source"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert payload["never_scanned"] == 1
        assert {"bucket": "never_scanned", "count": 1} in payload["buckets"]
        assert {"bucket": "scanned_24h", "count": 1} in payload["buckets"]
        assert {"bucket": "scanned_older", "count": 1} in payload["buckets"]

    print("PASS")