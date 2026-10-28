from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ScanFreshnessBuckets(BaseModel):
    never_scanned: int
    under_24h: int
    one_to_seven_days: int
    over_seven_days: int


class ScanFreshnessSource(BaseModel):
    registry_source: Optional[str]
    count: int


class ScanFreshnessResponse(BaseModel):
    total: int
    buckets: ScanFreshnessBuckets
    stale_seen_count: int
    by_source: List[ScanFreshnessSource]


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_scan_freshness_distribution(db: Session) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    one_day = timedelta(days=1)
    seven_days = timedelta(days=7)

    rows = db.query(McpServerRegistry).all()

    buckets = {
        "never_scanned": 0,
        "under_24h": 0,
        "one_to_seven_days": 0,
        "over_seven_days": 0,
    }
    source_counts: Dict[Optional[str], int] = {}
    stale_seen_count = 0

    for row in rows:
        last_scanned = _utc(row.last_scanned)
        last_seen = _utc(row.last_seen)

        if last_scanned is None:
            buckets["never_scanned"] += 1
        else:
            age = now - last_scanned
            if age < one_day:
                buckets["under_24h"] += 1
            elif age <= seven_days:
                buckets["one_to_seven_days"] += 1
            else:
                buckets["over_seven_days"] += 1

            if last_seen is not None and last_seen < last_scanned:
                stale_seen_count += 1

        source = row.registry_source
        source_counts[source] = source_counts.get(source, 0) + 1

    by_source = [
        {"registry_source": source, "count": count}
        for source, count in sorted(
            source_counts.items(), key=lambda item: (item[0] is not None, str(item[0]))
        )
    ]

    return {
        "total": len(rows),
        "buckets": buckets,
        "stale_seen_count": stale_seen_count,
        "by_source": by_source,
    }


def get_freshness_distribution(db: Session) -> Dict[str, Any]:
    return get_scan_freshness_distribution(db)


def calculate_scan_freshness_distribution(db: Session) -> Dict[str, Any]:
    return get_scan_freshness_distribution(db)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        McpServerRegistry(
            server_id="server-never",
            name="Never scanned",
            risk_tier="unknown",
            first_seen=now - timedelta(days=40),
            last_seen=now - timedelta(hours=1),
            last_scanned=None,
            scan_count=0,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="server-recent",
            name="Recent",
            risk_tier="low",
            first_seen=now - timedelta(days=2),
            last_seen=now - timedelta(minutes=5),
            last_scanned=now - timedelta(hours=1),
            scan_count=2,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="server-three-days",
            name="Three days old",
            risk_tier="medium",
            first_seen=now - timedelta(days=10),
            last_seen=now - timedelta(hours=2),
            last_scanned=now - timedelta(days=3),
            scan_count=1,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="server-thirty-days",
            name="Thirty days old",
            risk_tier="high",
            first_seen=now - timedelta(days=60),
            last_seen=now - timedelta(hours=3),
            last_scanned=now - timedelta(days=30),
            scan_count=4,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="server-stale-seen",
            name="Stale seen",
            risk_tier="low",
            first_seen=now - timedelta(days=4),
            last_seen=now - timedelta(days=2),
            last_scanned=now - timedelta(hours=5),
            scan_count=1,
            registry_source="seed",
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(seeded_rows)
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/scan-freshness", response_model=ScanFreshnessResponse)
    def scan_freshness(session: Session = Depends(get_session)):
        return get_scan_freshness_distribution(session)

    response = TestClient(that_app).get("/api/registry/scan-freshness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["buckets"]["never_scanned"] == 1
    assert payload["stale_seen_count"] == 1

    print("PASS")