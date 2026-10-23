from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _scan_bucket(days_since_scan: Optional[int]) -> str:
    if days_since_scan is None:
        return "never_scanned"
    if days_since_scan < 1:
        return "lt_1d"
    if days_since_scan < 7:
        return "lt_7d"
    if days_since_scan < 30:
        return "lt_30d"
    return "gte_30d"


def get_scan_staleness(
    session: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    cutoff_days = max(0, int(days))
    now = datetime.now(timezone.utc)

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
        McpServerRegistry.confidence,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.registry_source,
    )
    rows = session.execute(statement).all()

    bucket_counts = {
        "never_scanned": 0,
        "lt_1d": 0,
        "lt_7d": 0,
        "lt_30d": 0,
        "gte_30d": 0,
    }
    stale_records: List[Dict[str, Any]] = []
    source_counts: Dict[Any, Dict[str, int]] = {}

    for row in rows:
        last_scanned = row.last_scanned
        days_since_scan: Optional[int] = None
        if last_scanned is not None:
            scanned_at = _utc_datetime(last_scanned)
            elapsed = now - scanned_at
            days_since_scan = max(0, int(elapsed.total_seconds() // 86400))

        bucket = _scan_bucket(days_since_scan)
        bucket_counts[bucket] += 1

        source = row.registry_source
        if source not in source_counts:
            source_counts[source] = {"servers": 0, "stale_count": 0}
        source_counts[source]["servers"] += 1

        is_stale = days_since_scan is None or days_since_scan >= cutoff_days
        if is_stale:
            source_counts[source]["stale_count"] += 1
            stale_records.append(
                {
                    "server_id": row.server_id,
                    "name": row.name,
                    "verdict": row.verdict,
                    "risk_tier": row.risk_tier,
                    "days_since_scan": days_since_scan,
                    "scan_count": row.scan_count,
                }
            )

    stale_records.sort(
        key=lambda item: (
            item["days_since_scan"] is not None,
            item["days_since_scan"] if item["days_since_scan"] is not None else 0,
        ),
        reverse=True,
    )

    return {
        "days": cutoff_days,
        "total_servers": len(rows),
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in bucket_counts
        ],
        "stale_servers": stale_records[:200],
        "sources": [
            {
                "registry_source": source,
                "servers": counts["servers"],
                "stale_count": counts["stale_count"],
            }
            for source, counts in sorted(
                source_counts.items(),
                key=lambda item: str(item[0]) if item[0] is not None else "",
            )
        ],
    }


get_registry_scan_staleness = get_scan_staleness


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    test_now = datetime.now(timezone.utc)
    seeded_servers = [
        McpServerRegistry(
            server_id="never-scanned",
            name="Never scanned",
            verdict="unknown",
            risk_tier="unknown",
            trust_score=0.0,
            confidence=0.0,
            first_seen=test_now - timedelta(days=60),
            last_seen=test_now,
            last_scanned=None,
            scan_count=0,
            registry_source="catalog",
        ),
        McpServerRegistry(
            server_id="scanned-today",
            name="Scanned today",
            verdict="safe",
            risk_tier="low",
            trust_score=0.95,
            confidence=0.99,
            first_seen=test_now - timedelta(days=10),
            last_seen=test_now,
            last_scanned=test_now - timedelta(hours=1),
            scan_count=4,
            registry_source="catalog",
        ),
        McpServerRegistry(
            server_id="scanned-40-days",
            name="Scanned 40 days ago",
            verdict="risky",
            risk_tier="high",
            trust_score=0.2,
            confidence=0.8,
            first_seen=test_now - timedelta(days=90),
            last_seen=test_now,
            last_scanned=test_now - timedelta(days=40),
            scan_count=2,
            registry_source="catalog",
        ),
        McpServerRegistry(
            server_id="scanned-2-days",
            name="Scanned 2 days ago",
            verdict="safe",
            risk_tier="low",
            trust_score=0.9,
            confidence=0.95,
            first_seen=test_now - timedelta(days=15),
            last_seen=test_now,
            last_scanned=test_now - timedelta(days=2),
            scan_count=3,
            registry_source="manual",
        ),
    ]

    with TestingSession() as db_session:
        db_session.add_all(seeded_servers)
        db_session.commit()

        def override_get_session():
            db = TestingSession()
            try:
                yield db
            finally:
                db.close()

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_get_session

        @that_app.get("/api/registry/scan-staleness")
        def scan_staleness(days: int = 30, session: Session = Depends(get_session)):
            return get_scan_staleness(session=session, days=days)

        response = TestClient(that_app).get(
            "/api/registry/scan-staleness",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        counts = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert counts["never_scanned"] == 1
        assert sum(counts.values()) == 4
        assert any(
            item["server_id"] == "scanned-40-days"
            for item in payload["stale_servers"]
        )
        assert sum(item["servers"] for item in payload["sources"]) == 4

    print("PASS")