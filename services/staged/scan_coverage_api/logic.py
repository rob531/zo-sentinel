"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the real application registry through the application SQLAlchemy session
and computes scan-coverage buckets without modifying application data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_scan_coverage(db: Session) -> Dict[str, object]:
    """Return scan-recency buckets and the never-scanned risk-tier breakdown."""
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
            McpServerRegistry.risk_tier,
        )
    ).all()

    now = _utc_now()
    day = timedelta(days=1)
    buckets = {
        "never_scanned": 0,
        "scanned_24h": 0,
        "scanned_7d": 0,
        "scanned_30d": 0,
        "stale_over_30d": 0,
    }
    tier_breakdown: Dict[str, int] = {}

    for _server_id, scan_count, last_scanned, risk_tier in rows:
        if scan_count is None or scan_count == 0:
            buckets["never_scanned"] += 1
            tier = risk_tier or "unknown"
            tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
            continue

        scanned_at = _as_utc(last_scanned)
        if scanned_at is None:
            buckets["stale_over_30d"] += 1
            continue

        age = now - scanned_at
        if age <= day:
            buckets["scanned_24h"] += 1
        elif age <= 7 * day:
            buckets["scanned_7d"] += 1
        elif age <= 30 * day:
            buckets["scanned_30d"] += 1
        else:
            buckets["stale_over_30d"] += 1

    return {
        "total": len(rows),
        "buckets": buckets,
        "tier_breakdown": tier_breakdown,
        "generated_at": now.isoformat(),
    }


def scan_coverage(db: Session) -> Dict[str, object]:
    """Compatibility alias for callers using the service name directly."""
    return get_scan_coverage(db)


def _self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="never-scanned",
                scan_count=0,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="recent",
                scan_count=1,
                last_scanned=now - timedelta(hours=2),
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="five-days",
                scan_count=1,
                last_scanned=now - timedelta(days=5),
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="stale",
                scan_count=1,
                last_scanned=now - timedelta(days=40),
                risk_tier="critical",
            ),
            McpServerRegistry(
                server_id="recently-seen",
                scan_count=0,
                last_seen=now - timedelta(hours=1),
                risk_tier="medium",
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()

    @that_app.get("/api/servers/scan-coverage")
    def scan_coverage_endpoint(db: Session = Depends(get_session)):
        return get_scan_coverage(db)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/servers/scan-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert payload["buckets"]["never_scanned"] == 2
        assert payload["buckets"]["stale_over_30d"] == 1

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()